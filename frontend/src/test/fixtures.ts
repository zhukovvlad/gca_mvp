import type { AdminUser } from "@/types/admin";

export const sampleAdminUsers: AdminUser[] = [
  { id: 1, email: "a.petrov@example.com", role: "admin", is_active: true, created_at: "2026-08-01T10:00:00Z" },
  { id: 2, email: "i.orlova@example.com", role: "member", is_active: true, created_at: "2026-08-01T11:00:00Z" },
];
