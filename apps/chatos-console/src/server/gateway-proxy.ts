import type { Request, Response } from 'express';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { ReadableStream } from 'node:stream/web';

export async function proxyGateway(req: Request, res: Response, gatewayBase: string) {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 120_000);
  const abortUpstream = () => controller.abort();
  const onClose = () => { if (!res.writableFinished) abortUpstream(); };
  req.once('aborted', abortUpstream);
  res.once('close', onClose);
  res.setHeader('Cache-Control', 'no-store');

  try {
    if (req.aborted || res.destroyed) return;
    const headers: Record<string, string> = {
      'content-type': 'application/json', 'accept-encoding': 'identity',
    };
    for (const name of ['cookie', 'accept', 'range', 'if-range', 'if-none-match', 'if-modified-since']) {
      const value = req.headers[name];
      if (typeof value === 'string') headers[name] = value;
    }
    const upstream = await fetch(`${gatewayBase}/api${req.url}`, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : JSON.stringify(req.body ?? {}),
      redirect: 'manual',
      signal: controller.signal,
    });
    res.status(upstream.status);
    // An allowlist keeps CORS/security and hop-by-hop headers local. Connection
    // can nominate additional hop-by-hop fields, including otherwise safe ones.
    const connectionFields = new Set((upstream.headers.get('connection') || '').toLowerCase().split(',').map(value => value.trim()));
    for (const name of [
      'content-type', 'content-disposition', 'content-range', 'accept-ranges',
      'etag', 'last-modified', 'location', 'retry-after', 'www-authenticate',
    ]) {
      const value = upstream.headers.get(name);
      if (value && !connectionFields.has(name)) res.setHeader(name, value);
    }
    // Fetch decodes compressed bodies but retains their original headers.
    // Never apply an encoded length (or encoding) to the decoded byte stream.
    const encoding = upstream.headers.get('content-encoding');
    const length = upstream.headers.get('content-length');
    if (length && (!encoding || encoding === 'identity') && !connectionFields.has('content-length')) {
      res.setHeader('content-length', length);
    }
    if (!connectionFields.has('set-cookie')) {
      for (const cookie of upstream.headers.getSetCookie()) res.appendHeader('set-cookie', cookie);
    }
    if (!upstream.body) {
      res.end();
      return;
    }
    res.flushHeaders();
    // Pipeline propagates backpressure and cancels both ends on abort/error.
    // The deadline covers headers AND body, including a stalled downstream.
    await pipeline(Readable.fromWeb(upstream.body as ReadableStream<Uint8Array>), res, { signal: controller.signal });
  } catch (error) {
    controller.abort();
    if (res.destroyed) return;
    if (res.headersSent) {
      res.destroy(error instanceof Error ? error : new Error(String(error)));
    } else {
      res.status(timedOut ? 504 : 503).json({
        error: timedOut ? 'gateway_timeout' : 'gateway_unavailable',
        message: timedOut ? 'gateway 请求超时' : 'gateway 不可用',
      });
    }
  } finally {
    clearTimeout(timeout);
    req.off('aborted', abortUpstream);
    res.off('close', onClose);
  }
}
