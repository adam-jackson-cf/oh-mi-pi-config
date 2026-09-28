/** Counter names are `webhook.<outcome>` in past tense. */
export class Metrics {
  readonly counters: Record<string, number> = {};

  increment(name: string): void {
    this.counters[name] = (this.counters[name] ?? 0) + 1;
  }
}
