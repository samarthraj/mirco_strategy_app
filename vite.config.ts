import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import type { IncomingMessage, ServerResponse } from 'http'
import httpProxy from 'http-proxy'

// Dynamic proxy: reads the target from the X-Proxy-Target header
function dynamicProxyPlugin() {
  const proxy = httpProxy.createProxyServer({ secure: false, changeOrigin: true })

  proxy.on('proxyRes', (proxyRes, _req, _res) => {
    // Rewrite cookies so they work on localhost
    const setCookie = proxyRes.headers['set-cookie']
    if (setCookie) {
      proxyRes.headers['set-cookie'] = setCookie.map((c: string) =>
        c.replace(/;\s*domain=[^;]*/gi, '')
          .replace(/;\s*path=[^;]*/gi, '; Path=/')
          .replace(/;\s*secure/gi, '')
          .replace(/;\s*samesite=[^;]*/gi, '; SameSite=Lax')
      )
    }
    // Remove any redirect Location that points to the upstream server
    // (e.g. OAuth redirects) — let the client see the actual status
    delete proxyRes.headers['content-security-policy']
  })

  let defaultTarget = 'https://demo.microstrategy.com/MicroStrategyLibrary'

  return {
    name: 'dynamic-proxy',
    configureServer(server: any) {
      // Endpoint to update the default target at runtime
      server.middlewares.use('/__proxy_target', (req: IncomingMessage, res: ServerResponse) => {
        let body = ''
        req.on('data', (chunk: any) => { body += chunk })
        req.on('end', () => {
          try {
            const { target } = JSON.parse(body)
            if (target) {
              defaultTarget = target.replace(/\/+$/, '')
              res.writeHead(200, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ target: defaultTarget }))
            } else {
              res.writeHead(400)
              res.end('Missing target')
            }
          } catch {
            res.writeHead(400)
            res.end('Invalid JSON')
          }
        })
      })

      // Proxy all /api/* requests to the current target
      server.middlewares.use('/api', (req: IncomingMessage, res: ServerResponse) => {
        // connect middleware strips the mount path, so restore /api prefix
        req.url = '/api' + (req.url || '')
        // Strip browser Origin/Referer headers to avoid CORS rejection by upstream
        delete req.headers['origin']
        delete req.headers['referer']
        const target = defaultTarget
        proxy.web(req, res, { target, cookieDomainRewrite: '' }, (err) => {
          console.error('Proxy error:', err.message)
          res.writeHead(502)
          res.end('Proxy error: ' + err.message)
        })
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), dynamicProxyPlugin()],
})
