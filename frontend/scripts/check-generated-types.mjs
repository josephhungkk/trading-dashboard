import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const tempDir = mkdtempSync(join(tmpdir(), "dashboard-openapi-"));
const tempOpenApi = join(tempDir, "openapi.json");
const tempTypes = join(tempDir, "api-generated.ts");
// PATH inherited as-is — never bake developer-machine paths into a script
// the CI runs (architect-review acef860c HIGH). UV_CACHE_DIR fallback supports
// sandboxed runners where the default uv cache is read-only.
const env = {
  ...process.env,
  UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? "/tmp/uv-cache",
};

try {
  const openApiResult = spawnSync(
    "uv",
    ["run", "python", "-m", "app.scripts.dump_openapi"],
    { cwd: join(root, "backend"), env },
  );
  if (openApiResult.status !== 0) {
    process.stderr.write(openApiResult.stderr);
    process.exit(openApiResult.status ?? 1);
  }
  const openApi = openApiResult.stdout;
  writeFileSync(tempOpenApi, openApi);

  const typesResult = spawnSync(
    "pnpm",
    ["exec", "openapi-typescript", tempOpenApi, "-o", tempTypes],
    {
      cwd: join(root, "frontend"),
      env,
    },
  );
  process.stdout.write(typesResult.stdout);
  process.stderr.write(typesResult.stderr);
  if (typesResult.status !== 0) {
    process.exit(typesResult.status ?? 1);
  }

  const generated = readFileSync(tempTypes);
  const committed = readFileSync(
    join(root, "frontend", "src", "services", "api-generated.ts"),
  );

  if (!generated.equals(committed)) {
    console.error("Run 'pnpm gen:types' to refresh.");
    process.exitCode = 1;
  }
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
