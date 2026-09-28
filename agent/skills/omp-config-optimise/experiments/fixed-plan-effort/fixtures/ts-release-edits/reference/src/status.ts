export enum JobStatus {
  Pending = "pending",
  Running = "running",
  Succeeded = "succeeded",
  Failed = "failed",
  Cancelled = "cancelled",
}

export function describeStatus(status: JobStatus): string {
  switch (status) {
    case JobStatus.Pending:
      return "waiting to run";
    case JobStatus.Running:
      return "currently running";
    case JobStatus.Succeeded:
      return "completed successfully";
    case JobStatus.Failed:
      return "completed with an error";
    case JobStatus.Cancelled:
      return "cancelled by user";
  }
}
