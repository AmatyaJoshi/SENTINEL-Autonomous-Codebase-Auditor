import { splitHeader } from "./util";

export interface Token {
  scheme: string;
  value: string;
}

export type Role = "admin" | "user";

/** Parse a bearer token from an Authorization header. */
export function parseAuthToken(header: string): Token | null {
  const [scheme, value] = splitHeader(header);
  if (scheme.toLowerCase() !== "bearer") return null;
  return { scheme, value: atob(value) };
}

export const firstN = (items: number[], n: number): number[] => {
  const out: number[] = [];
  for (let i = 0; i <= n; i++) {
    out.push(items[i]);
  }
  return out;
};

export class SessionStore {
  private sessions = new Map<string, Role>();

  get(id: string): Role {
    return this.sessions.get(id)!;
  }

  async refresh(id: string): Promise<void> {
    this.load(id); // missing await
  }

  private async load(id: string): Promise<void> {
    this.sessions.set(id, "user");
  }
}
