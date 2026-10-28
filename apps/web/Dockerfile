# syntax=docker/dockerfile:1
# -----------------------------------------------------------------------------
# Claude Duet web - Next.js 15 on :3000
# Build context is apps/web (see docker-compose.yml).
#   docker build -t duet-web apps/web
#
# This image deliberately uses the plain `next build` + `next start` path rather
# than output:'standalone' - next.config.mjs is owned elsewhere and may not set
# it. That means node_modules ships in the runtime image; it is the portable
# option, not the smallest one.
# -----------------------------------------------------------------------------

# ---------- stage 1: dependencies ----------
FROM node:22-alpine AS deps
WORKDIR /app

# devDependencies are required: typescript/tailwind/postcss run during `next build`.
# So this is a full install - `npm ci --omit=dev` would break the build.
COPY package.json package-lock.json ./
RUN npm ci

# ---------- stage 2: build ----------
FROM node:22-alpine AS builder
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

# NEXT_PUBLIC_* values are inlined at build time by next.config.mjs.
ARG NEXT_PUBLIC_API_BASE=http://localhost:8787
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# git does not track empty directories, so a fresh clone may have no public/.
# Create it here so the runtime COPY below can never fail.
RUN mkdir -p /app/public

# ---------- stage 3: runtime ----------
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root user with a fixed uid/gid. BusyBox short flags, not GNU long ones:
# alpine's addgroup/adduser are applets and do not reliably accept --system etc.
# uid 1000 is already taken by the image's own `node` user, hence 10001.
RUN addgroup -g 10001 -S nodejs \
 && adduser  -u 10001 -S -G nodejs nextjs

# node_modules comes from `deps` (not `builder`) so the build's own scratch files
# are not carried forward. `next start` itself only needs the `next` runtime dep.
COPY --from=deps    --chown=nextjs:nodejs /app/node_modules ./node_modules
COPY --from=builder --chown=nextjs:nodejs /app/.next        ./.next
COPY --from=builder --chown=nextjs:nodejs /app/public       ./public
COPY --from=builder --chown=nextjs:nodejs /app/package.json /app/package-lock.json ./
COPY --from=builder --chown=nextjs:nodejs /app/next.config.mjs ./

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/ >/dev/null 2>&1 || exit 1

# package.json: "start": "next start -p 3000"
CMD ["npm","run","start"]
