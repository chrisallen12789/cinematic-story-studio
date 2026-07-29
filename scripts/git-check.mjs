import { capture } from "./lib/process.mjs";
import { repositoryRoot } from "./lib/paths.mjs";

async function checkedGit(args, failureMessage) {
  const result = await capture("git", args, {
    allowFailure: true,
    cwd: repositoryRoot,
    label: "Git integrity check",
  });
  if (result.code !== 0) {
    throw new Error(failureMessage);
  }
}

async function main() {
  const options = new Set(process.argv.slice(2));
  for (const option of options) {
    if (!["--staged", "--clean"].includes(option)) {
      throw new Error("Usage: node scripts/git-check.mjs [--staged] [--clean]");
    }
  }

  const diffArguments = ["diff"];
  if (options.has("--staged")) {
    diffArguments.push("--cached");
  }
  diffArguments.push("--check");
  await checkedGit(
    diffArguments,
    "Git found whitespace errors. Run the equivalent `git diff --check` locally to inspect them.",
  );

  if (options.has("--clean")) {
    await checkedGit(
      ["diff", "--quiet", "--exit-code"],
      "A verification command changed tracked files. Inspect `git status` before proceeding.",
    );
    await checkedGit(
      ["diff", "--cached", "--quiet", "--exit-code"],
      "The CI checkout unexpectedly contains staged changes.",
    );
    const untracked = await capture(
      "git",
      ["ls-files", "--others", "--exclude-standard", "-z"],
      {
        cwd: repositoryRoot,
        label: "Untracked file check",
      },
    );
    if (untracked.stdout.length > 0) {
      throw new Error(
        "A verification command created an unignored file. Inspect `git status`; filenames were not printed.",
      );
    }
  }

  process.stdout.write("Git diff safeguards passed; diff content was not printed.\n");
}

main().catch((error) => {
  process.stderr.write(`Git safeguard: ${error.message}\n`);
  process.exitCode = 1;
});
