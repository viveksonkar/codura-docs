# Builds every web app and serves them with nginx (Admin Console at /, widget at /widget/v1/).
# Build context: repo root.
FROM node:20.18.1-alpine AS build
RUN npm install -g pnpm@9.15.0
WORKDIR /web
COPY web/package.json web/pnpm-workspace.yaml web/pnpm-lock.yaml web/tsconfig.base.json ./
COPY web/packages ./packages
COPY web/apps ./apps
COPY web/scripts ./scripts
RUN pnpm install --frozen-lockfile && pnpm -r build

FROM nginx:1.27.3-alpine AS runtime
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/apps/admin/dist /usr/share/nginx/html
COPY --from=build /web/apps/widget/dist /usr/share/nginx/widget/v1
HEALTHCHECK --interval=15s --timeout=3s CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
