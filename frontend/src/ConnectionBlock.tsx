import { Button, Card, Group, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { checkConnection, CONNECTION_KEY, type Connection } from './api/connection';
import { words } from './words';

/** Текст состояния: до первого итога — «спрашиваем», затем — итог словами клиента. */
function stateText(connection: Connection | undefined): string {
  if (!connection) return words.connection.asking;
  return connection.ok ? words.connection.ok : connection.text;
}

/**
 * Блок «Связь с сервером» на заглушке (Ч1.4д). Состояние — словами в области `role="status"`: текст
 * меняется без перехода и объявляется (F31). Повтор — только кнопкой; двойное нажатие — один
 * запрос; пока ответа нет, кнопка в загрузке, а на экране прежний итог (F32, F33, F36).
 */
export function ConnectionBlock() {
  const { data, isFetching, refetch } = useQuery({
    queryKey: CONNECTION_KEY,
    queryFn: ({ signal }) => checkConnection(signal),
  });
  return (
    <Card mt="xl" p="lg">
      <Title order={2}>{words.connection.title}</Title>
      <Group mt="sm" justify="space-between" align="center" gap="md">
        <Text role="status">{stateText(data)}</Text>
        <Button loading={isFetching} onClick={() => void refetch({ cancelRefetch: false })}>
          {words.connection.retry}
        </Button>
      </Group>
    </Card>
  );
}
