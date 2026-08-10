FROM node:20-alpine AS build

WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=101:101 /ui/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080
