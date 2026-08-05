FROM node:22-alpine AS build

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@11.9.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY apps/user-web/package.json ./apps/user-web/package.json
COPY apps/merchant-web/package.json ./apps/merchant-web/package.json
COPY apps/platform-web/package.json ./apps/platform-web/package.json
COPY packages/web-ui/package.json ./packages/web-ui/package.json

RUN pnpm install --frozen-lockfile

COPY apps ./apps
COPY packages ./packages

ARG APP
ARG VITE_API_BASE_URL
ARG VITE_TEXT_WS_URL
ARG VITE_AUDIO_WS_URL
ARG VITE_MERCHANT_WEB_URL
ARG VITE_PLATFORM_WEB_URL

ENV VITE_API_BASE_URL="${VITE_API_BASE_URL}" \
    VITE_TEXT_WS_URL="${VITE_TEXT_WS_URL}" \
    VITE_AUDIO_WS_URL="${VITE_AUDIO_WS_URL}" \
    VITE_MERCHANT_WEB_URL="${VITE_MERCHANT_WEB_URL}" \
    VITE_PLATFORM_WEB_URL="${VITE_PLATFORM_WEB_URL}"

RUN pnpm --filter "@voice-shopping/${APP}-web" build

FROM nginx:1.27-alpine

COPY deploy/nginx/spa.conf /etc/nginx/conf.d/default.conf
ARG APP
COPY --from=build /app/apps/${APP}-web/dist /usr/share/nginx/html

EXPOSE 80

