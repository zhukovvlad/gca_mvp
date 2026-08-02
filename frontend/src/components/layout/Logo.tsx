import { Link } from "react-router-dom";
import { FileSpreadsheet } from "lucide-react";

export function Logo() {
  return (
    <Link
      to="/"
      className="flex items-center gap-2 text-fg hover:text-fg"
      aria-label="УПД Трекер — на главную"
    >
      <FileSpreadsheet size={18} className="text-accent" />
      <span className="font-serif text-base leading-none font-medium tracking-tight">
        УПД&nbsp;Трекер
      </span>
    </Link>
  );
}
