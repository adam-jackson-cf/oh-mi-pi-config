export {};
const workspace = process.argv[2];
const { JobStatus, describeStatus } = await import(`${workspace}/src/status.ts`);
const { DEFAULT_TIMEOUT_MS, MAX_JOB_NAME_LENGTH } = await import(`${workspace}/src/constants.ts`);
const packageJson = await Bun.file(`${workspace}/package.json`).json();
const changelog = await Bun.file(`${workspace}/CHANGELOG.md`).text();
let passed = 0;
const total = 6;
function check(value: boolean): void { if (value) passed += 1; }
check(packageJson.version === "1.4.1");
check(JobStatus.Cancelled === "cancelled");
check(describeStatus(JobStatus.Cancelled) === "cancelled by user");
check(DEFAULT_TIMEOUT_MS === 45_000);
check(MAX_JOB_NAME_LENGTH === 80);
check(changelog === "# Changelog\n\n## 1.4.0\n\n- Added worker health reporting.\n\n## 1.4.1\n\n- Added cancelled job status and increased the default timeout.\n");
console.log(JSON.stringify({ passed, total }));
process.exit(passed === total ? 0 : 1);
