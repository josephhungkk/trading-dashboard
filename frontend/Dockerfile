# Phase 0: produces a static-file nginx image. Phase 0 docker-compose runs `pnpm dev`
# instead of this nginx image for local dev. Phase 1 wires nginx + LE certs on the VPS.
#
# Build not verified in Phase 0 — Docker is not yet installed on this NUC.
# Verification happens at Task 19 (docker-compose up) once Docker Desktop or docker-ce
# is installed. If node:24-alpine base image is unavailable at build time, fall back
# to node:22-alpine and flag it.

FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

FROM node:24-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN corepack enable && pnpm build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# nginx SPA config is NOT baked in for Phase 0 — frontend dev uses `pnpm dev` via
# docker-compose. Phase 1 mounts nginx/spa.conf into the image or replaces
# /etc/nginx/conf.d/default.conf at the VPS deploy step.
