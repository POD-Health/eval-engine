/**
 * PodHealth Promptfoo Provider (JavaScript)
 *
 * Uses Python for Cognito login (boto3) and native fetch for Piper API calls.
 * Token is cached for 50 minutes to avoid re-login on every question.
 */

import { execFileSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { fetchWithProxy } from '../src/util/fetch/index.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Load podhealth/.env so provider works both from CLI and UI
function loadEnv(envPath) {
  if (!fs.existsSync(envPath)) {
    return;
  }
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) {
      continue;
    }
    const idx = trimmed.indexOf('=');
    if (idx === -1) {
      continue;
    }
    const key = trimmed.slice(0, idx).trim();
    const val = trimmed
      .slice(idx + 1)
      .trim()
      .replace(/^["']|["']$/g, '');
    if (!process.env[key]) {
      process.env[key] = val;
    }
  }
}

loadEnv(path.join(__dirname, '.env'));

// In-memory token cache
const tokenCache = {
  token: null,
  expiresAt: 0,
};

async function getToken() {
  const now = Date.now();

  if (tokenCache.token && now < tokenCache.expiresAt) {
    console.error('[PodHealth] Using cached Cognito token');
    return tokenCache.token;
  }

  const clean = (v) => (v ?? '').trim().replace(/^["']|["']$/g, '');
  const region = clean(process.env.AWS_REGION);
  const clientId = clean(process.env.COGNITO_CLIENT_ID);
  const email = clean(process.env.TEST_USER_EMAIL);
  const password = clean(process.env.TEST_USER_PASSWORD);

  console.error(`[PodHealth] Logging into Cognito as ${email}...`);

  // Use Python + boto3 for Cognito auth (proven to work)
  const script = `
import boto3, json, sys
client = boto3.client('cognito-idp', region_name='${region}')
res = client.initiate_auth(
  ClientId='${clientId}',
  AuthFlow='USER_PASSWORD_AUTH',
  AuthParameters={'USERNAME': '${email}', 'PASSWORD': '${password}'}
)
print(res['AuthenticationResult']['IdToken'])
`;

  const token = execFileSync('python3', ['-c', script], { encoding: 'utf8' }).trim();

  tokenCache.token = token;
  tokenCache.expiresAt = now + 50 * 60 * 1000;

  console.error('[PodHealth] Cognito login successful, token cached for 50 minutes');
  return token;
}

async function parseSse(response) {
  const text = await response.text();
  let fullText = '';

  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed === 'data: [DONE]') {
      continue;
    }

    if (trimmed.startsWith('data: ')) {
      const data = trimmed.slice(6);
      try {
        const parsed = JSON.parse(data);
        if (parsed.content) {
          fullText += parsed.content;
        } else if (parsed.text) {
          fullText += parsed.text;
        } else if (parsed.delta?.text) {
          fullText += parsed.delta.text;
        } else if (parsed.delta?.content) {
          fullText += parsed.delta.content;
        } else if (typeof parsed.delta === 'string') {
          fullText += parsed.delta;
        } else if (parsed.message) {
          fullText += parsed.message;
        }
      } catch {
        fullText += data;
      }
    }
  }

  return fullText.trim();
}

async function callPiper(prompt) {
  const token = await getToken();

  const baseUrl = process.env.DATA_AGENT_BASE_URL.replace(/\/$/, '').replace(/^"|"$/g, '');
  const diagnosticId = process.env.PARENT_DIAGNOSTIC_ID.replace(/^"|"$/g, '');
  const childId = (process.env.CHILD1_ID ?? '').replace(/^"|"$/g, '');

  const url = `${baseUrl}/agno-query-sql-agent`;

  console.error(`[PodHealth] Sending question: ${prompt.slice(0, 80)}...`);

  const payload = {
    stream: true,
    diagnostic_ids: [diagnosticId],
    stakeholder: 'parent',
    question: `For patient ${childId}: ${prompt}`,
    conversation_id: `eval_${Date.now()}`,
    current_date: new Date().toISOString().split('T')[0],
    timezone: 'UTC',
  };

  let response = await fetchWithProxy(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (response.status === 401) {
    console.error('[PodHealth] 401 received, refreshing token...');
    tokenCache.token = null;
    tokenCache.expiresAt = 0;
    const freshToken = await getToken();
    response = await fetchWithProxy(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${freshToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  if (!response.ok) {
    const err = await response.text();
    return { error: `API error ${response.status}: ${err.slice(0, 300)}` };
  }

  const text = await parseSse(response);
  if (!text) {
    return { error: 'Piper returned an empty response' };
  }

  console.error(`[PodHealth] Response received (${text.length} chars)`);
  return { output: text };
}

// Promptfoo expects a class with a callApi method for file:// providers
export default class PodHealthProvider {
  constructor(options) {
    this.options = options;
  }

  id() {
    return 'podhealth-piper';
  }

  async callApi(prompt) {
    try {
      return await callPiper(prompt);
    } catch (err) {
      return { error: `Provider error: ${err.message}` };
    }
  }
}
