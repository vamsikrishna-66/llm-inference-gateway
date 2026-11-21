import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const outcomeErrors = new Rate('unexpected_outcomes');

export const options = {
    scenarios: {
        fallback: { executor: 'shared-iterations', vus: 2, iterations: 12, exec: 'fallback' },
        rejected: { executor: 'shared-iterations', vus: 2, iterations: 12, exec: 'rejected' },
        stalled: { executor: 'shared-iterations', vus: 2, iterations: 12, exec: 'stalled' },
    },
    thresholds: {
        unexpected_outcomes: ['rate==0'],
        'http_req_duration{scenario:fallback}': ['p(95)<1000'],
        'http_req_duration{scenario:rejected}': ['p(95)<1000'],
        'http_req_duration{scenario:stalled}': ['p(95)<2500'],
    },
    summaryTrendStats: ['min', 'med', 'p(95)', 'p(99)', 'max'],
};

function generate(prompt, status) {
    return http.post('http://127.0.0.1:8000/generate',
        JSON.stringify({ model: 'synthetic', prompt }), {
            headers: { 'Content-Type': 'application/json' },
            responseCallback: http.expectedStatuses(status),
            timeout: '5s',
        });
}

export function fallback() {
    const ok = check(generate('fallback', 200), {
        'fallback completes': r => r.status === 200 && r.body.includes('"response":"fallback"')
            && r.body.includes('"done":true') && !r.body.includes('event: error'),
    });
    outcomeErrors.add(!ok);
}

export function rejected() {
    const ok = check(generate('both-reject', 502), {
        'both upstreams unavailable': r => r.status === 502,
    });
    outcomeErrors.add(!ok);
}

export function stalled() {
    const ok = check(generate('stall', 200), {
        'deadline interrupts the primary stream': r => r.status === 200
            && r.body.includes('"response":"primary"') && r.body.includes('event: error')
            && r.body.includes('model deadline exceeded') && !r.body.includes('"done":true'),
    });
    outcomeErrors.add(!ok);
}
