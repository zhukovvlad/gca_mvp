import { http } from "msw";

import { server } from "./server";

type Method = "get" | "post" | "patch" | "put" | "delete";

/**
 * Задерживает ответ на запросы `method path` до `release()`, после чего их обслуживает
 * штатный обработчик из `handlers.ts` (обработчик, не вернувший ответа, передаёт запрос дальше).
 * Так запрос «висит» ровно столько, сколько нужно тесту, чтобы увидеть состояние ожидания.
 */
export function holdRequests(method: Method, path: string) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const seen = { count: 0 };
  server.use(
    http[method](path, async () => {
      seen.count += 1;
      await gate;
    })
  );
  return { release, seen };
}
