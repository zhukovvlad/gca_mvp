import { Outlet } from "react-router-dom";
import { TopNav } from "./TopNav";

export function AppShell() {
  return (
    <div className="min-h-screen bg-page text-fg">
      <TopNav />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
