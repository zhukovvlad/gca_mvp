import { Navigate, useParams } from "react-router-dom";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { getHandbookArticle } from "./articles";

export function HandbookArticle() {
  const { slug } = useParams<{ slug: string }>();
  const article = getHandbookArticle(slug);

  if (!article) {
    return <Navigate to="/handbook" replace />;
  }

  const { Component } = article;

  return (
    <div className="container-page py-8">
      <Breadcrumbs items={[{ label: "Справочник", to: "/handbook" }, { label: article.title }]} />
      <Component />
    </div>
  );
}

export default HandbookArticle;
