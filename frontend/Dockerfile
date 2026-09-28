# Stage 1: build the static bundle.
FROM node:22-alpine AS build
WORKDIR /app
# pnpm is pinned by the "packageManager" field in package.json; corepack fetches it.
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# Stage 2: the map's label glyphs (#62), fetched once at build time and served
# from this origin: at runtime the map reaches nothing but its own host
# (criterion 29). Noto Sans as PBF glyph ranges from Protomaps' basemaps-assets,
# pinned to a commit and checked against its hash. The fonts are SIL OFL 1.1
# (OFL.txt ships beside them); the sprites in the same archive carry no stated
# licence and are not used.
FROM alpine:3.20 AS glyphs
ARG BASEMAPS_ASSETS_COMMIT=028c18f713baecad011301ff7a69acc39bcc2ae7
ARG BASEMAPS_ASSETS_SHA256=57e40e8c512bd8042d0a3a251f19d0d1c8523ad963c666c3c6643bada4dc92d0
RUN apk add --no-cache curl \
 && curl -fsSL -o /tmp/assets.tar.gz "https://codeload.github.com/protomaps/basemaps-assets/tar.gz/${BASEMAPS_ASSETS_COMMIT}" \
 && echo "${BASEMAPS_ASSETS_SHA256}  /tmp/assets.tar.gz" | sha256sum -c - \
 && mkdir -p /fonts \
 && tar -xzf /tmp/assets.tar.gz -C /fonts --strip-components=2 "basemaps-assets-${BASEMAPS_ASSETS_COMMIT}/fonts" \
 && ls "/fonts/Noto Sans Regular/0-255.pbf" /fonts/OFL.txt

# Stage 3: serve dist/ and proxy /api to the api service.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=glyphs /fonts /usr/share/nginx/html/fonts
# Map tiles are bind-mounted here from data/tiles/ by compose; the directory
# exists in the image so nginx starts even when no extract has been downloaded.
RUN mkdir -p /tiles
EXPOSE 80
