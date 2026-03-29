"""
PodHealth Promptfoo Provider

Handles Cognito login + Piper API call for each eval question.
Token is cached for 50 minutes to avoid re-login on every question.
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
import boto3
from botocore.exceptions import ClientError

# Load .env from the same directory as this script
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# In-memory token cache (survives across questions in one eval run)
_token_cache = {
    'token': None,
    'expires_at': 0
}


def _get_token():
    """Return cached JWT or login to Cognito to get a fresh one."""
    now = time.time()

    if _token_cache['token'] and now < _token_cache['expires_at']:
        logger.info("Using cached Cognito token")
        return _token_cache['token']

    aws_region = os.environ['AWS_REGION']
    cognito_client_id = os.environ['COGNITO_CLIENT_ID']
    email = os.environ['TEST_USER_EMAIL']
    password = os.environ['TEST_USER_PASSWORD']

    logger.info(f"Logging into Cognito as {email}...")

    client = boto3.client('cognito-idp', region_name=aws_region)

    response = client.initiate_auth(
        ClientId=cognito_client_id,
        AuthFlow='USER_PASSWORD_AUTH',
        AuthParameters={
            'USERNAME': email,
            'PASSWORD': password
        }
    )

    token = response['AuthenticationResult']['IdToken']

    # Cache for 50 min (Cognito tokens expire after 60 min)
    _token_cache['token'] = token
    _token_cache['expires_at'] = now + (50 * 60)

    logger.info("Cognito login successful, token cached for 50 minutes")
    return token


def _parse_sse(response):
    """
    Parse SSE streaming response into plain text.
    Handles JSON data lines and raw text chunks.
    """
    full_text = ""

    for chunk in response.iter_content(chunk_size=None):
        if not chunk:
            continue

        decoded = chunk.decode('utf-8', errors='replace')

        for line in decoded.split('\n'):
            line = line.strip()

            if not line or line == 'data: [DONE]':
                continue

            if line.startswith('data: '):
                data = line[6:]
                try:
                    parsed = json.loads(data)

                    if 'content' in parsed:
                        full_text += parsed['content']
                    elif 'text' in parsed:
                        full_text += parsed['text']
                    elif 'delta' in parsed:
                        delta = parsed['delta']
                        if isinstance(delta, dict):
                            full_text += delta.get('text', delta.get('content', ''))
                        elif isinstance(delta, str):
                            full_text += delta
                    elif 'message' in parsed:
                        full_text += parsed['message']
                except json.JSONDecodeError:
                    full_text += data

    return full_text.strip()


def call_api(prompt, options, context):
    """
    Promptfoo Python provider entry point.

    Called once per test question. Returns the Piper AI response
    as plain text for grading.

    Args:
        prompt: The question text (from {{question}} in YAML)
        options: Provider config from YAML
        context: Test context including vars

    Returns:
        dict: {"output": response_text} or {"error": error_message}
    """
    try:
        token = _get_token()

        base_url = os.environ['DATA_AGENT_BASE_URL'].rstrip('/')
        diagnostic_id = os.environ['PARENT_DIAGNOSTIC_ID']
        child_id = os.environ.get('CHILD1_ID', '')

        url = f"{base_url}/agno-query-sql-agent"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "stream": True,
            "diagnostic_ids": [diagnostic_id],
            "stakeholder": "parent",
            "question": f"For patient {child_id}: {prompt}",
            "conversation_id": f"eval_{int(datetime.now().timestamp())}",
            "current_date": datetime.now().strftime("%Y-%m-%d"),
            "timezone": "UTC"
        }

        logger.info(f"Sending question to Piper: {prompt[:80]}...")
        logger.info("Calling Piper API now...")

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=120
        )

        logger.info(f"Response status: {response.status_code}")

        if response.status_code == 401:
            logger.warning("401 received, clearing token cache and retrying...")
            _token_cache['token'] = None
            _token_cache['expires_at'] = 0

            token = _get_token()
            headers["Authorization"] = f"Bearer {token}"

            response = requests.post(
                url,
                json=payload,
                headers=headers,
                stream=True,
                timeout=30
            )

            logger.info(f"Retry response status: {response.status_code}")

        if response.status_code != 200:
            return {"error": f"API error {response.status_code}: {response.text[:300]}"}

        text = _parse_sse(response)

        if not text:
            return {"error": "Piper returned an empty response"}

        logger.info(f"Response received ({len(text)} chars)")
        return {"output": text}

    except ClientError as e:
        return {"error": f"Cognito auth failed: {e}"}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out after 30 seconds"}
    except Exception as e:
        return {"error": f"Provider error: {type(e).__name__}: {e}"}