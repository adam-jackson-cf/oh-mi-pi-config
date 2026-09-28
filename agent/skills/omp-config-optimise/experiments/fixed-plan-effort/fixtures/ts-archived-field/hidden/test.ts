export {};
const workspace = process.argv[2];
const { createTask, serializeTask, validateTaskPayload } = await import(`${workspace}/src/index.ts`);
const { sampleTask } = await import(`${workspace}/src/demo.ts`);
let passed = 0;
const total = 7;
function check(value: boolean): void { if (value) passed += 1; }
const legacy = createTask({ id: "a", title: "Legacy" });
const archived = createTask({ id: "b", title: "Stored", archived: true });
check(JSON.stringify(legacy) === JSON.stringify({ id: "a", title: "Legacy", archived: false }));
check(JSON.stringify(archived) === JSON.stringify({ id: "b", title: "Stored", archived: true }));
check(serializeTask(legacy) === '{"id":"a","title":"Legacy","archived":false}');
check(serializeTask(archived) === '{"id":"b","title":"Stored","archived":true}');
check(validateTaskPayload({ id: "a", title: "T" }));
check(!validateTaskPayload({ id: "a", title: "T", archived: "false" }));
check(JSON.stringify(sampleTask) === JSON.stringify({ id: "welcome", title: "Read the guide", archived: false }));
console.log(JSON.stringify({ passed, total }));
process.exit(passed === total ? 0 : 1);
