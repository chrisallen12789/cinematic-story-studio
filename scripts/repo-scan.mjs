import { execFile } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";
import { repositoryRoot } from "./lib/paths.mjs";

const execFileAsync = promisify(execFile);
const MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024;
const MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024;
const privateManuscriptMarker = new RegExp(
  [
    ["-----BEGIN PRIVATE", "MANUSCRIPT-----"].join(" "),
    ["\\[\\[PRIVATE", "MANUSCRIPT CONTENT\\]\\]"].join(" "),
  ].join("|"),
  "gi",
);

const forbiddenExtensions = new Map([
  [".aac", "generated-audio"],
  [".db", "project-database"],
  [".db-journal", "project-database"],
  [".db-shm", "project-database"],
  [".db-wal", "project-database"],
  [".doc", "private-source-document"],
  [".docx", "private-source-document"],
  [".epub", "private-source-document"],
  [".flac", "generated-audio"],
  [".key", "credential-file"],
  [".m4a", "generated-audio"],
  [".m4b", "generated-audio"],
  [".mp3", "generated-audio"],
  [".ogg", "generated-audio"],
  [".p12", "credential-file"],
  [".pdf", "private-source-document"],
  [".pem", "credential-file"],
  [".pfx", "credential-file"],
  [".sqlite", "project-database"],
  [".sqlite3", "project-database"],
  [".wav", "generated-audio"],
]);

const textRules = [
  {
    id: "private-key",
    expression: /-----BEGIN (?:EC |OPENSSH |PGP |RSA )?PRIVATE KEY-----/g,
  },
  {
    id: "aws-access-key",
    expression: /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g,
  },
  {
    id: "github-token",
    expression: /\bgh[pousr]_[A-Za-z0-9]{30,}\b/g,
  },
  {
    id: "provider-api-key",
    expression: /\bsk-[A-Za-z0-9_-]{24,}\b/g,
  },
  {
    id: "credential-in-url",
    expression:
      /\b(?:mongodb(?:\+srv)?|mysql|postgres(?:ql)?):\/\/[^:\s/]+:[^@\s/]+@/gi,
  },
  {
    id: "absolute-user-path",
    expression:
      /(?:\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+|\/(?:Users|home)\/[^/\s]+)/g,
  },
  {
    id: "private-manuscript-marker",
    expression: privateManuscriptMarker,
  },
];

function normalizePath(file) {
  return file.replaceAll("\\", "/").replace(/^\.\/+/, "");
}

function safePathForReport(file) {
  return normalizePath(file).replaceAll("\r", "?").replaceAll("\n", "?");
}

export function inspectPath(file) {
  const normalized = normalizePath(file);
  const lower = normalized.toLowerCase();
  const basename = path.posix.basename(lower);
  const extension = path.posix.extname(lower);

  if (
    (basename === ".env" || basename.startsWith(".env.")) &&
    basename !== ".env.example"
  ) {
    return "environment-file";
  }

  const extensionRule = forbiddenExtensions.get(extension);
  if (extensionRule) {
    return extensionRule;
  }

  if (
    /(^|\/)(?:node_modules|__pycache__|\.venv|\.runtime|\.pyinstaller)(?:\/|$)/.test(
      lower,
    )
  ) {
    return "generated-dependency-or-runtime";
  }

  if (
    /(^|\/)(?:dist|build|out|release|installer-output|playwright-report|test-results|coverage)(?:\/|$)/.test(
      lower,
    )
  ) {
    return "generated-build-or-test-output";
  }

  if (/^apps\/local-service\/data(?:\/|$)/.test(lower)) {
    return "local-application-data";
  }

  if (/^apps\/desktop\/build-resources\/service(?:\/|$)/.test(lower)) {
    return "staged-service-binary";
  }

  const localContentMatch = /^(local-projects|local-models|local-cache|local-renders)\/(.+)$/.exec(
    lower,
  );
  if (localContentMatch && localContentMatch[2] !== ".gitkeep") {
    return "private-local-content";
  }

  return null;
}

export function inspectText(text) {
  const findings = [];
  for (const rule of textRules) {
    rule.expression.lastIndex = 0;
    for (const match of text.matchAll(rule.expression)) {
      const index = match.index ?? 0;
      const line = text.slice(0, index).split("\n").length;
      findings.push({ line, rule: rule.id });
    }
  }
  return findings;
}

async function gitFileList(scope) {
  const args =
    scope === "staged"
      ? ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
      : ["ls-files", "-z"];
  const { stdout } = await execFileAsync("git", args, {
    cwd: repositoryRoot,
    encoding: "buffer",
    maxBuffer: MAX_GIT_OUTPUT_BYTES,
    windowsHide: true,
  });
  return stdout
    .toString("utf8")
    .split("\0")
    .filter(Boolean);
}

async function stagedContent(file) {
  const { stdout } = await execFileAsync("git", ["show", `:${file}`], {
    cwd: repositoryRoot,
    encoding: "buffer",
    maxBuffer: MAX_GIT_OUTPUT_BYTES,
    windowsHide: true,
  });
  return stdout;
}

async function workingTreeContent(file) {
  const absolutePath = path.join(repositoryRoot, file);
  const metadata = await stat(absolutePath);
  if (metadata.size > MAX_TEXT_FILE_BYTES) {
    return { oversized: true, content: null };
  }
  return { oversized: false, content: await readFile(absolutePath) };
}

async function scan(scope) {
  const findings = [];
  const files = await gitFileList(scope);

  for (const file of files) {
    const pathRule = inspectPath(file);
    if (pathRule) {
      findings.push({ file, line: null, rule: pathRule });
      continue;
    }

    let content;
    if (scope === "staged") {
      try {
        content = await stagedContent(file);
      } catch {
        findings.push({ file, line: null, rule: "unreadable-staged-content" });
        continue;
      }
      if (content.length > MAX_TEXT_FILE_BYTES) {
        findings.push({ file, line: null, rule: "oversized-tracked-file" });
        continue;
      }
    } else {
      const result = await workingTreeContent(file);
      if (result.oversized) {
        findings.push({ file, line: null, rule: "oversized-tracked-file" });
        continue;
      }
      content = result.content;
    }

    if (!content || content.includes(0)) {
      continue;
    }

    const textFindings = inspectText(content.toString("utf8"));
    for (const finding of textFindings) {
      findings.push({ file, ...finding });
    }
  }

  return { filesScanned: files.length, findings };
}

async function main() {
  const argument = process.argv[2] ?? "--tracked";
  if (!["--tracked", "--staged"].includes(argument)) {
    throw new Error("Usage: node scripts/repo-scan.mjs [--tracked|--staged]");
  }

  const scope = argument.slice(2);
  const result = await scan(scope);
  if (result.findings.length > 0) {
    process.stderr.write(
      `${scope} repository scan found ${result.findings.length} prohibited item(s):\n`,
    );
    for (const finding of result.findings) {
      const location = finding.line
        ? `${safePathForReport(finding.file)}:${finding.line}`
        : safePathForReport(finding.file);
      process.stderr.write(`- ${location} [${finding.rule}]\n`);
    }
    process.stderr.write("Finding values are intentionally redacted.\n");
    process.exitCode = 1;
    return;
  }

  process.stdout.write(
    `${scope} repository scan passed (${result.filesScanned} files; values not printed).\n`,
  );
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  main().catch((error) => {
    process.stderr.write(`Repository scan failed safely: ${error.message}\n`);
    process.exitCode = 1;
  });
}
