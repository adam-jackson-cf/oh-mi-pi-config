export type GuardCheck = (args: unknown[]) => void;

export function patch<Owner>(owner: Owner, name: string, check: GuardCheck): void {
  const native: unknown = Object.getOwnPropertyDescriptor(owner, name)?.value;
  if (!(native instanceof Function)) return;
  Object.defineProperty(owner, name, {
    value: function guarded(this: Owner, ...args: unknown[]) {
      check(args);
      return native.apply(this, args);
    },
  });
}
