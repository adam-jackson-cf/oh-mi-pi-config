export class ConfigError extends Error {
  constructor(readonly variable: string, message: string) {
    super(message);
    this.name = "ConfigError";
  }
}
