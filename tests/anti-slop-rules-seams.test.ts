import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, describe, test } from "node:test";
import { z } from "zod";

type Fixture = {
	readonly file: string;
	readonly behaviour: string;
	readonly source: string;
	readonly findings: readonly string[];
};

type RuleSetting = "error" | readonly ["error", { readonly allowInTypeGuards: boolean }];

type LintOverride = {
	readonly files: readonly string[];
	readonly rules: Readonly<Record<string, RuleSetting>>;
};

const repoRoot = fileURLToPath(new URL("..", import.meta.url));
const pluginPath = join(repoRoot, "tools/oxlint/anti-slop/index.ts");
const ruleNames = [
	"no-chained-type-assertions",
	"no-conditional-empty-object-spread",
	"no-known-value-widening",
	"no-module-mocking",
	"no-object-parameters",
	"no-runtime-typeof",
	"no-shape-in-symbol-names",
	"no-unknown-parameters",
	"no-unknown-returns",
	"no-unknown-type-aliases",
	"no-unsafe-dictionary-type",
	"no-widen-then-assert",
];

const lintReport = z.object({
	diagnostics: z.array(
		z.object({
			code: z.string().optional(),
			filename: z.string(),
			labels: z.array(z.object({ span: z.object({ line: z.number() }) })),
		}),
	),
});

const fixtures: readonly Fixture[] = [
	{
		file: "no-chained-type-assertions.ts",
		behaviour: "no-chained-type-assertions reports as and angle-bracket chains, parenthesised or not, and allows const-only chains",
		source: `declare const b: number;
const a = b as unknown as string;
const c = <string>(<unknown>b);
const e = (b as unknown) as string;
const g = [1] as const as const;
const h = b as number;
export { a, c, e, g, h };
`,
		findings: ["no-chained-type-assertions:2", "no-chained-type-assertions:3", "no-chained-type-assertions:4"],
	},
	{
		file: "no-conditional-empty-object-spread.ts",
		behaviour: "no-conditional-empty-object-spread reports an object spread of a conditional with an empty-object branch only",
		source: `declare const c: boolean;
export const o = { ...(c ? { a: 1 } : {}) };
export const p = { ...(c ? {} : { a: 1 }) };
export const q = [...(c ? [1] : [])];
export const r = { ...(c ? { a: 1 } : { b: 2 }) };
`,
		findings: ["no-conditional-empty-object-spread:2", "no-conditional-empty-object-spread:3"],
	},
	{
		file: "no-known-value-widening.ts",
		behaviour: "no-known-value-widening reports known values flowing into broad or anonymous targets and skips empty accumulators, named contracts and rewritten bindings",
		source: `type Bag = Record<string, number>;
type Box<T> = Record<string, T>;
type Named = { a: number };
type M = { [K in string]: number };
type M2 = { [K in "a" | "b"]: number };
type Keyed = { [K in PropertyKey]: number };
type KeyedU = { [K in string | number]: number };
type Sub<T> = { [K in T]: number };
type SubS = Sub<string>;
type Ro = Readonly<{ [k: string]: number }>;
type AliasOfAlias = Bag;
type Unk = unknown;
type Obj = object;
type Wrap<T> = { v: T };
type Gen<T> = T;
type Rec3 = Rec4;
type Rec4 = Rec3;
type Nested = Readonly<Bag>;
type NoArg = Readonly;
type Q = NS.Thing;
declare const globalThing: number;
export const a: unknown = 1;
export const b: object = {};
export const c: { [k: string]: number } = {};
export const c2: { [k: string]: number } = { a: 1 };
export const d: { a: number } = { a: 1 };
export const e: {} = {};
export const f: Record<string, number> = { a: 1 };
export const g: Readonly<unknown> = 1;
export const h: { [K in "a"]: number } = { a: 1 };
export const i: Bag = { a: 1 };
export const j: Box<number> = { a: 1 };
export const j2: Box<number> = {};
export const k: Named = { a: 1 };
export const l: M = { a: 1 };
export const l2: M2 = { a: 1, b: 2 };
export const l3: Keyed = { a: 1 };
export const l4: KeyedU = { a: 1 };
export const l5: SubS = { a: 1 };
export const l6: Ro = { a: 1 };
export const l7: AliasOfAlias = { a: 1 };
export const l8: Unk = 1;
export const l9: Obj = [1];
export const l10: Wrap<number> = { v: 1 };
export const l11: Gen<unknown> = 1;
export const l12: Rec3 = 1;
export const l13: Nested = { a: 1 };
export const l14: NoArg = 1;
export const l15: Q = 1;
export const l16: string = "a";
const src = { a: 1 };
export const m: unknown = src;
let src2 = { a: 1 };
src2 = { a: 2 };
export const n: unknown = src2;
export const o: unknown = globalThing;
export const o2: unknown = (src);
export class C { p: unknown = 1; accessor q: unknown = 2; r: unknown; s = 1; accessor t = 2; }
let s1: unknown;
s1 = 1;
let s2 = 1;
s2 += 1;
const holder = { v: 1 };
holder.v = 2;
let s3: string;
s3 = "a";
export function t(): unknown { return 1; }
export function t2(): unknown { return; }
export const u = (): unknown => 1;
export const u2 = (): unknown => { return 1; };
export class D { m(): unknown { return 1; } "lit"(): unknown { return 1; } [globalThing](): unknown { return 1; } #p(): unknown { return 1; } }
export default function (): unknown { return 1; }
export const v = 1 as unknown;
export const w = 1 as unknown as string;
export const x = <unknown>1;
export const y = <unknown>(<string>"a");
export const z = { f: function (): unknown { return 1; } };
export { s1, s2, s3 };
`,
		findings: ["no-known-value-widening:22", "no-known-value-widening:23", "no-known-value-widening:25", "no-known-value-widening:26", "no-known-value-widening:28", "no-known-value-widening:29", "no-known-value-widening:30", "no-known-value-widening:31", "no-known-value-widening:32", "no-known-value-widening:35", "no-known-value-widening:37", "no-known-value-widening:38", "no-known-value-widening:39", "no-known-value-widening:40", "no-known-value-widening:41", "no-known-value-widening:42", "no-known-value-widening:43", "no-known-value-widening:47", "no-known-value-widening:52", "no-known-value-widening:57", "no-known-value-widening:58", "no-known-value-widening:58", "no-known-value-widening:60", "no-known-value-widening:67", "no-known-value-widening:69", "no-known-value-widening:70", "no-known-value-widening:71", "no-known-value-widening:71", "no-known-value-widening:71", "no-known-value-widening:71", "no-known-value-widening:72", "no-known-value-widening:73", "no-known-value-widening:75", "no-known-value-widening:76", "no-known-value-widening:77"],
	},
	{
		file: "no-module-mocking--global.ts",
		behaviour: "no-module-mocking leaves calls on locally declared vi and jest objects alone",
		source: `declare const vi: { mock(name: string): void; fn(): void };
declare const jest: { doMock(name: string): void };
declare const other: { mock(name: string): void };
declare const name: "mock";
vi.mock("x");
jest["doMock"]("x");
vi.fn();
other.mock("x");
jest[name]("x");
`,
		findings: [],
	},
	{
		file: "no-module-mocking--undeclared.ts",
		behaviour: "no-module-mocking reports mock, doMock and unstable_mockModule on the global vi and jest, dotted or by literal key",
		source: `vi.mock("x");
jest.mock("y");
mock("z");
class A extends Object { constructor() { super(); } }
jest["doMock"]("x");
vi["unstable_mockModule"]("x");
jest["other"]("x");
jest[name]("x");
vi.fn();
export { A };
`,
		findings: ["no-module-mocking:1", "no-module-mocking:2", "no-module-mocking:5", "no-module-mocking:6"],
	},
	{
		file: "no-module-mocking--imports.ts",
		behaviour: "no-module-mocking reports module mocks through vi from vitest and jest from @jest/globals only",
		source: `import { vi as v } from "vitest";
import { jest } from "@jest/globals";
import { vi } from "other";
import * as all from "vitest";
v.unstable_mockModule("x");
jest.mock("x");
vi.mock("x");
all.vi.mock("x");
`,
		findings: ["no-module-mocking:5", "no-module-mocking:6"],
	},
	{
		file: "no-module-mocking--local.ts",
		behaviour: "no-module-mocking leaves a local object named vi alone",
		source: `const vi = { mock(name: string) { return name; } };
vi.mock("x");
`,
		findings: [],
	},
	{
		file: "no-object-parameters.ts",
		behaviour: "no-object-parameters reports object-typed parameters of every function form, through local aliases, and skips names a type parameter, infer or mapped key shadows",
		source: `type O = object;
export type EO = O;
type U = string | (object);
type R = R2;
type R2 = R;
type G<T> = T;
export function f(a: object) { return a; }
export function g(a: O) { return a; }
export const h = (a: U) => a;
export function k<O>(a: O) { return a; }
export function m(...rest: EO) { return rest; }
export class C { constructor(private readonly a: object) { this.a; } }
export function n({ a }: object) { return a; }
export function p(a: object = {}) { return a; }
export function q(a: G<object>) { return a; }
export function s(a: R) { return a; }
export interface I { m(a: object): void; new (a: object): I; (a: object): void }
export type F = (a: object) => void;
export type Ctor = new (a: object) => void;
export declare function d(a: object): void;
export const fe = function (a: string, b?: object) { return [a, b]; };
export function t(a: string) { return a; }
export type Inf<T> = T extends (a: infer O) => void ? (a: O) => void : (a: O) => void;
export type InfNested<T> = T extends [infer O, (infer P extends string)] ? (a: O, b: P) => void : never;
export type Mp = { [O in "k"]: (a: O) => void };
export type MpName = { [O in "k" as ((a: O) => void) extends never ? O : "x"]: string };
export type MpConstraint = { [K in ((a: O) => void) extends never ? "a" : "b"]: K };
export class Gc<O> { m(a: O) { return a; } }
export interface Ig<O> { m(a: O): void }
export type Ag<O> = (a: O) => void;
`,
		findings: ["no-object-parameters:7", "no-object-parameters:8", "no-object-parameters:9", "no-object-parameters:11", "no-object-parameters:12", "no-object-parameters:13", "no-object-parameters:14", "no-object-parameters:17", "no-object-parameters:17", "no-object-parameters:17", "no-object-parameters:18", "no-object-parameters:19", "no-object-parameters:20", "no-object-parameters:21", "no-object-parameters:23", "no-object-parameters:27"],
	},
	{
		file: "no-reflect--global.ts",
		behaviour: "no-reflect-apply and no-reflect-get report the global Reflect methods, dotted or by literal key",
		source: `declare const f: () => void;
declare const o: object;
Reflect.apply(f, null, []);
Reflect["apply"](f, null, []);
Reflect.get(o, "k");
Reflect["get"](o, "k");
Reflect.has(o, "k");
f.apply(null, []);
`,
		findings: ["no-reflect-apply:3", "no-reflect-apply:4", "no-reflect-get:5", "no-reflect-get:6"],
	},
	{
		file: "no-reflect--local.ts",
		behaviour: "no-reflect-apply and no-reflect-get leave a local object named Reflect alone",
		source: `const Reflect = { apply() {}, get() {} };
Reflect.apply();
Reflect.get();
`,
		findings: [],
	},
	{
		file: "no-runtime-typeof.ts",
		behaviour: "no-runtime-typeof reports every typeof by default, inside type guards too",
		source: `declare const a: string;
export const t = typeof a;
export function g(x: string): x is string { return typeof x === "string"; }
`,
		findings: ["no-runtime-typeof:2", "no-runtime-typeof:3"],
	},
	{
		file: "no-runtime-typeof--guards.ts",
		behaviour: "no-runtime-typeof with allowInTypeGuards allows typeof only directly inside a type-guard function",
		source: `declare const a: string;
export const t = typeof a;
export function g(x: string): x is string { return typeof x === "string"; }
export const h = (x: string): boolean => typeof x === "string";
export function k(x: string): x is string { const inner = () => typeof x; return inner() === "string"; }
`,
		findings: ["no-runtime-typeof:2", "no-runtime-typeof:4", "no-runtime-typeof:5"],
	},
	{
		file: "no-shape-in-symbol-names.tsx",
		behaviour: "no-shape-in-symbol-names reports identifiers, private names and JSX names that contain shape",
		source: `export const userShape = 1;
export class A { #shapeX = 1; get x() { return this.#shapeX; } }
declare const ShapeView: () => null;
export const v = <ShapeView />;
export const plain = 2;
`,
		findings: ["no-shape-in-symbol-names:1", "no-shape-in-symbol-names:2", "no-shape-in-symbol-names:2", "no-shape-in-symbol-names:3", "no-shape-in-symbol-names:4"],
	},
	{
		file: "no-unknown-parameters.ts",
		behaviour: "no-unknown-parameters reports unknown parameters of every function form except one named cause",
		source: `export function f(a: unknown) { return a; }
export function g(cause: unknown) { return cause; }
export function h(...args: unknown) { return args; }
export function k(a: unknown = 1) { return a; }
export class C { constructor(public a: unknown) {} }
export function m({ a }: unknown) { return a; }
export type F = (a: unknown) => void;
export type Ctor = new (a: unknown) => void;
export interface I { m(a: unknown): void; new (a: unknown): I; (a: unknown): void }
export declare function d(a: unknown): void;
export const fe = function (a: string, ...rest: string[]) { return [a, rest]; };
export class D { constructor(public readonly cause: unknown = 1) {} }
`,
		findings: ["no-unknown-parameters:1", "no-unknown-parameters:3", "no-unknown-parameters:4", "no-unknown-parameters:5", "no-unknown-parameters:6", "no-unknown-parameters:7", "no-unknown-parameters:8", "no-unknown-parameters:9", "no-unknown-parameters:9", "no-unknown-parameters:9", "no-unknown-parameters:10"],
	},
	{
		file: "no-unknown-returns.ts",
		behaviour: "no-unknown-returns reports unknown, promised unknown and local aliases of unknown as return types, and skips generic, shadowed and inferred names",
		source: `type U = unknown;
export type G<T> = T;
type G2<T> = unknown;
type Cy = Cy2;
type Cy2 = Cy;
export function f(): unknown { return 1; }
export async function p(): Promise<unknown> { return 1; }
export function pl(): PromiseLike<unknown> { return Promise.resolve(1); }
export function un(): string | unknown { return 1; }
export function pa(): (unknown) { return 1; }
export function al(): U { return 1; }
export function ga(): G<string> { return "a"; }
export function g2(): G2 { return 1; }
export function sh<U>(x: U): U { return x; }
export function cy(): Cy { return 1; }
export function np(): Promise { return 1; }
export function s(): string { return "a"; }
export const ar = (): unknown => 1;
export type F = () => unknown;
export interface I { m(): unknown; new (): unknown; (): unknown }
export declare function d(): unknown;
export function n() { return 1; }
export type Inf<T> = T extends () => infer U ? () => U : () => U;
export class Gc<U> { m(): U { return this.m(); } }
`,
		findings: ["no-unknown-returns:6", "no-unknown-returns:7", "no-unknown-returns:8", "no-unknown-returns:9", "no-unknown-returns:10", "no-unknown-returns:11", "no-unknown-returns:18", "no-unknown-returns:19", "no-unknown-returns:20", "no-unknown-returns:20", "no-unknown-returns:20", "no-unknown-returns:21", "no-unknown-returns:23"],
	},
	{
		file: "no-unknown-type-aliases.ts",
		behaviour: "no-unknown-type-aliases reports aliases that resolve to unknown and skips generic and cyclic aliases",
		source: `type A = unknown;
type B = A;
type C = (unknown);
type D<T> = unknown;
type E = D;
type F = G;
type G = F;
export type H = unknown;
type I = string;
type J = Array<A>;
export type { A, B, C, D, E, F, G, I, J };
`,
		findings: ["no-unknown-type-aliases:1", "no-unknown-type-aliases:2", "no-unknown-type-aliases:3", "no-unknown-type-aliases:4", "no-unknown-type-aliases:8"],
	},
	{
		file: "no-unsafe-dictionary-type.ts",
		behaviour: "no-unsafe-dictionary-type reports dictionaries whose value type is an escape hatch, through wrappers and aliases",
		source: `import { Record as R0 } from "x";
interface Empty {}
interface E2 { a?: never }
interface Full { a: string }
type Dict<T> = Record<string, T>;
type Bag = Record<string, unknown>;
type D2<T = unknown> = Record<string, T>;
type D3<T> = Record<string, T>;
type Outer<U> = Inner<U>;
type Inner<V> = Record<string, V>;
type Rec = Rec2;
type Rec2 = Rec;
type Self<T> = T;
declare namespace NS { type Record<K, V> = Map<K, V>; }
export let a: Record<string, unknown>;
export let b: { [k: string]: any };
export let c: Record<string, object>;
export let d: Record<string, {}>;
export let e: Record<string, string | unknown>;
export let f: Record<string, unknown & {}>;
export let f2: Record<string, any & string>;
export let f3: Record<string, string & {}>;
export let g: Readonly<Record<string, unknown>>;
export let h: { [K in string]: unknown };
export let i: Partial<Record<string, Readonly<unknown>>>;
export let j: Pick<Record<string, unknown>, "a">;
export let k: Dict<unknown>;
export let l: Bag;
export let m: Record<string, Empty>;
export let m2: Record<string, E2>;
export let m3: Record<string, Full>;
export let n: { [k: string]: { a?: never } };
export interface Bag2 { [k: string]: unknown }
export class C { [k: string]: any }
export let o: R0<string, unknown>;
type Q = D2;
type Q3 = D3;
export let s: Outer<unknown>;
export let t: Record<string, Rec>;
export let u: Record<string, string>;
export let v: NS.Record<string, unknown>;
export let w: Record<string, Self<unknown>>;
export let x: Readonly<Record<string, readonly unknown[]>>;
export let y: Record<string, (unknown)>;
export let z: Required<{ [k: string]: object }>;
export let za: Partial;
export let zb: Omit<{ [k: string]: unknown }, "a">;
export let zc: { [K in string] };
export let zd: Record<string>;
export type { Q, Q3 };
`,
		findings: ["no-unsafe-dictionary-type:6", "no-unsafe-dictionary-type:15", "no-unsafe-dictionary-type:16", "no-unsafe-dictionary-type:17", "no-unsafe-dictionary-type:18", "no-unsafe-dictionary-type:19", "no-unsafe-dictionary-type:20", "no-unsafe-dictionary-type:21", "no-unsafe-dictionary-type:23", "no-unsafe-dictionary-type:24", "no-unsafe-dictionary-type:25", "no-unsafe-dictionary-type:26", "no-unsafe-dictionary-type:27", "no-unsafe-dictionary-type:29", "no-unsafe-dictionary-type:30", "no-unsafe-dictionary-type:32", "no-unsafe-dictionary-type:33", "no-unsafe-dictionary-type:34", "no-unsafe-dictionary-type:36", "no-unsafe-dictionary-type:38", "no-unsafe-dictionary-type:42", "no-unsafe-dictionary-type:44", "no-unsafe-dictionary-type:45", "no-unsafe-dictionary-type:47"],
	},
	{
		file: "no-unsafe-dictionary-type--shadowed.ts",
		behaviour: "no-unsafe-dictionary-type treats a local declaration of a built-in name as that local type",
		source: `type Partial<T> = Map<string, T>;
class Readonly<T> { v?: T }
function Required() {}
enum Pick { A }
interface Omit<T> { v: T }
export let a: Partial<unknown>;
export let b: Readonly<Record<string, unknown>>;
export { Readonly, Required, Pick };
export type { Omit };
`,
		findings: ["no-unsafe-dictionary-type:7"],
	},
	{
		file: "no-widen-then-assert.ts",
		behaviour: "no-widen-then-assert reports a const that widens known evidence and is asserted back to a narrower type in the same function",
		source: `type Foo = { x: number };
declare function make(): Foo;
declare const n: Foo;
export function cases() {
  const a: unknown = { x: 1 } as Foo;
  const b = a as Foo;
  const c = 1 as unknown;
  const d = c as number;
  const e: object = [1];
  const f = e as number[];
  const g: object = [1];
  const h = g as string;
  const i: Record<string, unknown> = { a: 1 };
  const j = i as { a: number };
  const k: Record<string, unknown> = { a: 1 };
  const l = k as Record<string, number>;
  const l2 = k as Readonly<Record<string, number>>;
  const l3 = k as Record<string, unknown>;
  const l4 = k as Map<string, number>;
  const m: object = n as Foo;
  const m2 = m as Foo;
  const q: unknown = 1;
  const r = q as unknown;
  const s = { a: 1 };
  const t: unknown = s;
  const u = t as { a: number };
  const v: Foo = make();
  const w: unknown = v;
  const x = w as Foo;
  let z: unknown = 1;
  const z2 = z as number;
  z = 2;
  const aa: Readonly<Record<string, unknown>> = { a: 1 };
  const ab = aa as { a: number };
  const ac: Record<string | number, any> = { a: 1 };
  const ad = ac as { a: number };
  const ae: Record<PropertyKey, unknown> = { a: 1 };
  const af = ae as { a: number };
  const ag: { [k: string]: unknown } = { a: 1 };
  const ah = ag as { a: number };
  const ai: object = [1];
  const aj = ai as readonly number[];
  const ak = ai as (() => void) & { a: 1 };
  const al = ai as [number];
  const am = ai as new () => Foo;
  const an = ai as { [K in "a"]: number };
  const ao = ai as {};
  const ap = ai as readonly string[];
  const aq: object = [1];
  const ar = <number[]>aq;
  const as2 = (aq) as Foo;
  const at = (ai as Foo);
  const au: Record<string, unknown, number> = {};
  const av = au as Foo;
  const aw: { [k: string]: unknown; b: number } = { a: 1, b: 2 };
  const ax = aw as Foo;
  const ay: { [k: string]: unknown; [j: number]: unknown } = {};
  const az = ay as Foo;
  const ba: Readonly = {};
  const bb = ba as Foo;
  const bc: object = makeLater();
  const bd = bc as Foo;
  const be: unknown = \`t\`;
  const bf = be as string;
  const bg: unknown = globalUnknown;
  const bh = bg as string;
  const bi: object = bj;
  const bk = bi as Foo;
  return [b, d, f, h, j, l, l2, l3, l4, m2, r, u, x, z2, ab, ad, af, ah, aj, ak, al, am, an, ao, ap, ar, as2, at, av, ax, az, bb, bd, bf, bh, bk];
}
declare function makeLater(): object;
declare const globalUnknown: unknown;
const bj: Foo = { x: 1 };
const top: unknown = 1;
export function later() { return top as number; }
const early = (top2 as number);
const top2: unknown = 1;
export function annotated(p: Foo) { const q: unknown = p; return q as Foo; }
export function annotatedBroad(p: unknown) { const q: unknown = p; return q as Foo; }
export function outerParam(p: Foo) { return () => { const q: unknown = p; return q as Foo; }; }
export function letSource() { let s = { a: 1 }; const t: unknown = s; return t as { a: number }; }
export function writtenSource() { const s = { a: 1 }; const t: unknown = s; return t as { a: number }; }
export function chain() { const s = { a: 1 }; const t = s; const u: unknown = t; return u as { a: number }; }
export function selfRef() { const s: unknown = s; return s as number; }
export function destructured() { const { s }: { s: unknown } = { s: 1 }; return s as number; }
export function asParent() { const s: unknown = 1; return (s as number) as const; }
export { early };
`,
		findings: ["no-widen-then-assert:6", "no-widen-then-assert:8", "no-widen-then-assert:10", "no-widen-then-assert:14", "no-widen-then-assert:16", "no-widen-then-assert:17", "no-widen-then-assert:21", "no-widen-then-assert:26", "no-widen-then-assert:29", "no-widen-then-assert:34", "no-widen-then-assert:36", "no-widen-then-assert:38", "no-widen-then-assert:40", "no-widen-then-assert:42", "no-widen-then-assert:43", "no-widen-then-assert:44", "no-widen-then-assert:45", "no-widen-then-assert:46", "no-widen-then-assert:48", "no-widen-then-assert:50", "no-widen-then-assert:64", "no-widen-then-assert:78", "no-widen-then-assert:82", "no-widen-then-assert:83", "no-widen-then-assert:86"],
	},
];

function lintOverride(
	files: readonly string[],
	rules: Readonly<Record<string, RuleSetting>>,
): LintOverride {
	return { files, rules };
}

function lintConfig(): string {
	const overrides = [
		...ruleNames.map((rule) => lintOverride([`${rule}*`], { [`anti-slop/${rule}`]: "error" })),
		lintOverride(["no-reflect--*"], {
			"anti-slop/no-reflect-apply": "error",
			"anti-slop/no-reflect-get": "error",
		}),
		lintOverride(["no-runtime-typeof--guards.ts"], {
			"anti-slop/no-runtime-typeof": ["error", { allowInTypeGuards: true }],
		}),
	];
	return JSON.stringify({
		plugins: [],
		categories: {},
		jsPlugins: [{ name: "anti-slop", specifier: pluginPath }],
		rules: {},
		overrides,
	});
}

describe("anti-slop oxlint plugin under the real linter", () => {
	const findings = new Map<string, string[]>();
	let fixtureDir = "";

	before(() => {
		fixtureDir = mkdtempSync(join(tmpdir(), "anti-slop-"));
		writeFileSync(join(fixtureDir, ".oxlintrc.json"), lintConfig());
		for (const fixture of fixtures) writeFileSync(join(fixtureDir, fixture.file), fixture.source);
		const run = spawnSync(
			"bunx",
			[
				"oxlint",
				"-c",
				join(fixtureDir, ".oxlintrc.json"),
				"--format",
				"json",
				...fixtures.map((fixture) => join(fixtureDir, fixture.file)),
			],
			{ cwd: repoRoot, encoding: "utf8", timeout: 60000 },
		);
		for (const diagnostic of lintReport.parse(JSON.parse(run.stdout)).diagnostics) {
			const code = diagnostic.code ?? "parse-error";
			if (code.startsWith("eslint(")) continue;
			const name = basename(diagnostic.filename);
			const entries = findings.get(name) ?? [];
			entries.push(`${code.replace(/^anti-slop\((.*)\)$/u, "$1")}:${diagnostic.labels[0]?.span.line}`);
			findings.set(name, entries);
		}
	});

	after(() => {
		rmSync(fixtureDir, { recursive: true, force: true });
	});

	for (const fixture of fixtures) {
		test(fixture.behaviour, () => {
			const found = (findings.get(fixture.file) ?? []).toSorted(
				(left, right) => Number(left.split(":")[1]) - Number(right.split(":")[1]) || left.localeCompare(right),
			);
			const expected = fixture.findings.toSorted(
				(left, right) => Number(left.split(":")[1]) - Number(right.split(":")[1]) || left.localeCompare(right),
			);
			assert.deepEqual(found, expected);
		});
	}
});
