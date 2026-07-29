import assert from "node:assert/strict";
import test from "node:test";
import { inspectPath, inspectText } from "../../scripts/repo-scan.mjs";

test("repository path policy allows only placeholders in private local roots", () => {
  assert.equal(inspectPath("local-projects/.gitkeep"), null);
  assert.equal(inspectPath("local-projects/story.md"), "private-local-content");
  assert.equal(inspectPath("apps/local-service/data/project.sqlite"), "project-database");
  assert.equal(inspectPath("render.wav"), "generated-audio");
  assert.equal(inspectPath(".env.example"), null);
  assert.equal(inspectPath(".env.local"), "environment-file");
});

test("text scan reports rule and line without returning matched values", () => {
  const privateKeyMarker = ["-----BEGIN", " PRIVATE KEY-----"].join("");
  const findings = inspectText(`safe first line\n${privateKeyMarker}\n`);

  assert.deepEqual(findings, [{ line: 2, rule: "private-key" }]);
  assert.equal(Object.hasOwn(findings[0], "value"), false);
});

test("policy prose about private manuscripts is not treated as manuscript content", () => {
  assert.deepEqual(
    inspectText("Never commit private manuscripts or generated audio."),
    [],
  );
});
