// Серверы на петле для тестов в среде Node: порт — свободный от системы, после теста — закрыты.
import { createServer, type RequestListener, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

const open: Server[] = [];

/** Сервер на петле, порт — свободный от системы; закрывает его `closeServers()` после теста. */
export function listen(handler: RequestListener): Promise<{ port: number; url: string }> {
  const server = createServer(handler);
  open.push(server);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo;
      resolve({ port, url: `http://127.0.0.1:${port}` });
    });
  });
}

/** Свободный от системы порт, где никто не слушает: сервер взял его и сразу отпустил. */
export async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  await new Promise<void>((resolve) => server.close(() => resolve()));
  return port;
}

/** После теста: все серверы `listen()` закрыты. */
export async function closeServers(): Promise<void> {
  await Promise.all(
    open.splice(0).map((s) => new Promise<void>((resolve) => s.close(() => resolve()))),
  );
}
