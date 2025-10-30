import express from 'express';
import cors from 'cors';
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import YAML from 'yaml';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT ? Number(process.env.PORT) : 4000;

app.use(cors());
app.use(express.json());

// Resolve repo root from server dir
const repoRoot = path.resolve(__dirname, '..', '..');
const testsDir = process.env.TESTS_DIR ? path.resolve(process.env.TESTS_DIR) : path.join(repoRoot, 'tests');

// Types for parsed YAML
interface TestYAML {
  fileType: string; // 'momentruc/test'
  id: string;
  name: string;
  description?: string;
  baseUrl?: string;
  schemaVersion?: string;
  retries?: number;
  envs?: Array<{ name: string; default?: boolean }>;
  disabled?: boolean;
  labels?: string[];
  steps?: any[];
}

async function listYamlFiles(dir: string) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      files.push(...(await listYamlFiles(full)));
    } else if (e.isFile() && e.name.endsWith('.yaml')) {
      files.push(full);
    }
  }
  return files;
}

async function loadTests() {
  try {
    const files = await listYamlFiles(testsDir);
    const testFiles = files.filter((f) => /\.test\.yaml$/i.test(f));
    const items: any[] = [];

    await Promise.all(
      testFiles.map(async (file) => {
        try {
          const raw = await fs.readFile(file, 'utf8');
          const doc = YAML.parse(raw) as TestYAML;
          if (!doc || typeof doc !== 'object') return;
          if (!doc.fileType || !/momenti[c|k]?\/test/i.test(doc.fileType)) return;
          const stat = await fs.stat(file);

          items.push({
            id: doc.id,
            name: doc.name,
            description: doc.description ?? '',
            baseUrl: doc.baseUrl ?? '',
            schemaVersion: doc.schemaVersion ?? null,
            retries: doc.retries ?? 0,
            disabled: !!doc.disabled,
            labels: doc.labels ?? [],
            filePath: path.relative(repoRoot, file),
            createdAt: stat.birthtime.toISOString(),
            updatedAt: stat.mtime.toISOString(),
            stepCount: Array.isArray(doc.steps) ? doc.steps.length : 0
          });
        } catch (e) {
          console.error('Failed to parse', file, e);
        }
      })
    );

    // Sort by updated desc by default
    items.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return items;
  } catch (e) {
    console.error('Error loading tests:', e);
    return [];
  }
}

app.get('/api/tests', async (_req, res) => {
  const tests = await loadTests();
  res.json({ tests });
});

app.get('/api/tests/:id', async (req, res) => {
  const tests = await loadTests();
  const t = tests.find((x: any) => x.id === req.params.id);
  if (!t) return res.status(404).json({ error: 'Not found' });

  try {
    const fullPath = path.join(repoRoot, t.filePath);
    const raw = await fs.readFile(fullPath, 'utf8');
    const doc = YAML.parse(raw) as TestYAML;
    res.json({
      ...t,
      envs: doc.envs ?? [],
      steps: doc.steps ?? []
    });
  } catch (e) {
    res.status(500).json({ error: 'Failed to load test file' });
  }
});

app.get('/health', (_req, res) => res.json({ ok: true }));

app.listen(PORT, () => {
  console.log(`Server listening on http://localhost:${PORT}`);
  console.log(`Reading tests from: ${testsDir}`);
});
