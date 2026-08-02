export const qk = {
  admin: {
    users: (q?: string, page?: number, pageSize?: number) =>
      ["admin", "users", q ?? "", page ?? 1, pageSize ?? 20] as const,
  },
};
