# Образ web: фронт собирает node, отдаёт nginx образа от его пользователя nginx (uid 101).
# Контекст — корень репозитория, белый список — Dockerfile.dockerignore рядом (нужен BuildKit).
# Базы — тег линии и дайджест: последний патч линии старше 14 суток от выхода версии
# (decisions Ч1.5б, срез 0). node — только стадия сборки, в итоговый образ не попадает.

# Сборка: пакеты только по замку; битая сборка или оракул сборки роняют сборку образа.
FROM node:24-alpine@sha256:a34ab6d7f6f4813f4d0c5469d74685604b962fbdbf5793a7bc7b4998ecb867c8 AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
RUN npm run check:dist

# Итог: в корне сайта — ровно dist/, в conf.d — только наш файл; pid и временные файлы — в /tmp:
# корень контейнера только для чтения. Правка pid проверяется: другой вид строки роняет сборку.
FROM nginx:1.30-alpine@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a AS web
RUN sed -i -e '/^user /d' -e 's,^pid .*,pid /tmp/nginx.pid;,' /etc/nginx/nginx.conf \
    && grep -qx 'pid /tmp/nginx.pid;' /etc/nginx/nginx.conf \
    && rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/*
COPY deploy/web/nginx.conf /etc/nginx/conf.d/web.conf
COPY deploy/web/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /build/dist/ /usr/share/nginx/html/
USER 101
EXPOSE 8080
