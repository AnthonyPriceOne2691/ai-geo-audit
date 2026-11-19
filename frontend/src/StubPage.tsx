import { Anchor, Container, Paper, Table, Text, Title } from '@mantine/core';

import { SERVICE_NAME } from './brand';
import { cssVar } from './theme/tokens';
import { words } from './words';

const TABLE_ID = 'samples-table';

type Tone = keyof typeof words.samples.status;

/** Статус на подложке — слово и цвет вместе: одним цветом статус не передаётся. Цвет — токенами:
 *  пропс цвета у Badge включил бы расчёт оттенков через color-mix(). */
function Status({ tone }: { tone: Tone }) {
  return (
    <Text
      component="span"
      display="inline-block"
      px="xs"
      py={4}
      size="sm"
      fw={600}
      style={{
        color: cssVar(tone),
        backgroundColor: cssVar(`${tone}Tint`),
        borderRadius: cssVar('radiusControl'),
      }}
    >
      {words.samples.status[tone]}
    </Text>
  );
}

/** Образцы вида (F20) — для снимков в обеих темах. Карточка — Paper: у Card свой фон библиотеки. */
function Samples() {
  const { samples } = words;
  return (
    <>
      <Paper mt="xl" p="lg">
        <Title order={2}>{samples.title}</Title>
        <Text mt="sm">{samples.text}</Text>
        <Text mt="xs" c="dimmed">
          {samples.muted}
        </Text>
        <Text mt="xs">
          <Anchor href={`#${TABLE_ID}`}>{samples.link}</Anchor>
        </Text>
      </Paper>
      {/* Таблица во всю рабочую область; центровка — из темы (F18). */}
      <Paper mt="md">
        <Table id={TABLE_ID}>
          <Table.Thead>
            <Table.Tr>
              {samples.columns.map((column) => (
                <Table.Th key={column}>{column}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {samples.rows.map(({ page, score, tone }) => (
              <Table.Tr key={page}>
                <Table.Td>{page}</Table.Td>
                <Table.Td>{score}</Table.Td>
                <Table.Td>
                  <Status tone={tone} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}

/** Заглушка: называет сервис и говорит, что на ней есть, — факт, а не обещание (F16). */
export function StubPage() {
  return (
    <Container component="main" py="xl">
      <Title order={1}>{SERVICE_NAME}</Title>
      <Text mt="sm">{words.stub.fact}</Text>
      <Samples />
    </Container>
  );
}
