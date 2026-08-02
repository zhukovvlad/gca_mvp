import { Fragment } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

interface BreadcrumbsProps {
  items: BreadcrumbItem[];
}

export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav aria-label="Хлебные крошки" className="flex items-center gap-1 text-xs">
      {items.map((item, idx) => {
        const isLast = idx === items.length - 1;
        return (
          <Fragment key={`${item.label}-${idx}`}>
            {idx > 0 && (
              <ChevronRight size={12} className="text-fg-tertiary" />
            )}
            {item.to && !isLast ? (
              <Link
                to={item.to}
                className="text-fg-secondary hover:text-fg"
              >
                {item.label}
              </Link>
            ) : (
              <span className="text-fg">{item.label}</span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}
