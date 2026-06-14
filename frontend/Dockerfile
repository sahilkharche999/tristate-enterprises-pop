# Frontend Dockerfile - build React/Vite app and serve with nginx
# Build stage
FROM node:22-alpine AS build
WORKDIR /app

# Enable pnpm via corepack (built into Node 20)
RUN corepack enable && corepack prepare pnpm@10 --activate

# Install dependencies
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --prefer-offline

# Accept backend URL at build time.
# Leave empty for Docker local dev so the browser uses relative URLs
# and nginx proxies API calls to the backend container.
# Railway / production deployments should set this as a build arg.
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL

# Copy source and build
COPY . .
RUN pnpm run build

# Production stage - nginx
FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html

# Remove default nginx.conf and provide a minimal one for SPA routing
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
