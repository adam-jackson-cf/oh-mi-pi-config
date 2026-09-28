export interface TaskInput {
  id: string;
  title: string;
  archived?: boolean;
}

export interface Task {
  id: string;
  title: string;
  archived: boolean;
}
