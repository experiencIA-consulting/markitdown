FROM node:22-alpine AS build

WORKDIR /app
COPY packages/markitdown-admin/package.json packages/markitdown-admin/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY packages/markitdown-admin/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY packages/markitdown-admin/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
