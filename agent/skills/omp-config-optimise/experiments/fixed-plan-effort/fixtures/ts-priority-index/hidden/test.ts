const workspace = process.argv[2];
const { dispatchOrder, getPreferredIndex } = await import(`${workspace}/src/index.ts`);

let passed = 0;
const total = 5;
function check(value: boolean): void { if (value) passed += 1; }
const front = [{ id: "a", priority: "urgent" }, { id: "b", priority: "normal" }] as const;
const middle = [{ id: "a", priority: "normal" }, { id: "b", priority: "urgent" }, { id: "c", priority: "normal" }] as const;
check(getPreferredIndex(front) === 0);
check(getPreferredIndex(middle) === 1);
check(getPreferredIndex([{ id: "a", priority: "normal" }]) === undefined);
check(JSON.stringify(dispatchOrder(front)) === JSON.stringify(["a", "b"]));
check(JSON.stringify(dispatchOrder(middle)) === JSON.stringify(["b", "a", "c"]));
console.log(JSON.stringify({ passed, total }));
process.exit(passed === total ? 0 : 1);
