# Stage 1: build the static bundle.
FROM node:22-alpine AS build
WORKDIR /app
# pnpm is pinned by the "packageManager" field in package.json; corepack fetches it.
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# Stage 2: serve dist/ and proxy /api to the api service.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Map tiles are bind-mounted here from data/tiles/ by compose; the directory
# exists in the image so nginx starts even when no extract has been downloaded.
RUN mkdir -p /tiles
EXPOSE 80
