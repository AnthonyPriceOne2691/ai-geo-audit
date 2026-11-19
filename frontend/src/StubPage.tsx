import { Container, Text, Title } from '@mantine/core';

import { SERVICE_NAME } from './brand';
import { words } from './words';

/** Заглушка: называет сервис и говорит, что на ней есть, — факт, а не обещание (F16). */
export function StubPage() {
  return (
    <Container component="main" py="xl">
      <Title order={1}>{SERVICE_NAME}</Title>
      <Text mt="sm">{words.stub.fact}</Text>
    </Container>
  );
}
