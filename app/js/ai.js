// AI marking via OpenRouter (OpenAI-compatible chat completions API).
import * as db from "./db.js";

const API = "https://openrouter.ai/api/v1";
export const DEFAULT_MODEL = "anthropic/claude-sonnet-5.5";

export async function config() {
  return {
    key: await db.setting("openrouterKey", ""),
    model: await db.setting("openrouterModel", DEFAULT_MODEL),
  };
}

export const enabled = async () => Boolean((await config()).key);

/** Vision-capable models, cheapest first. Public endpoint, no key needed. */
export async function listModels() {
  const res = await fetch(`${API}/models`);
  if (!res.ok) throw new Error(`OpenRouter models: HTTP ${res.status}`);
  const { data } = await res.json();
  return data
    .filter((m) => m.architecture?.input_modalities?.includes("image") && !m.id.endsWith(":batch"))
    .map((m) => ({
      id: m.id,
      name: m.name,
      structured: (m.supported_parameters ?? []).includes("structured_outputs"),
      // $ per 1M tokens
      input: Number(m.pricing?.prompt ?? 0) * 1e6,
      output: Number(m.pricing?.completion ?? 0) * 1e6,
    }))
    .sort((a, b) => a.input - b.input);
}

const SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["score", "max", "points", "feedback"],
  properties: {
    score: { type: "integer", description: "Total marks awarded" },
    max: { type: "integer", description: "Total marks available" },
    points: {
      type: "array",
      description: "One entry per question part / marking point in the mark scheme",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["part", "awarded", "available", "comment"],
        properties: {
          part: { type: "string", description: "e.g. 2(b)(i)" },
          awarded: { type: "integer" },
          available: { type: "integer" },
          comment: { type: "string", description: "Why marks were given or lost, citing mark scheme codes (M1, A1, B1, C1...)" },
        },
      },
    },
    feedback: { type: "string", description: "2-4 sentences: the main misconception or gap and how to fix it" },
  },
};

const SYSTEM = `You are a senior Cambridge International AS & A Level examiner.
Mark the student's answer strictly against the official mark scheme provided, following Cambridge conventions:
- M marks for method, A marks for accuracy (dependent on the related M mark), B marks independent, C marks for compensatory working (awarded implicitly if the final answer is correct).
- Allow error carried forward (ECF / FT) only where the mark scheme allows it.
- Accept equivalent wording and alternative valid methods; do not reward vague or irrelevant answers.
- Parts the student left blank score 0.
- If the student only answered some parts, still report every part so they see what they missed.
Be concise and specific. Address the student directly.`;

async function blobToDataURL(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(blob);
  });
}

/** Downscale a user photo so requests stay small (longest side ≤ 1600px, JPEG). */
export async function shrinkPhoto(file, maxSide = 1600) {
  const bmp = await createImageBitmap(file);
  const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const canvas = new OffscreenCanvas(Math.round(bmp.width * k), Math.round(bmp.height * k));
  canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return canvas.convertToBlob({ type: "image/jpeg", quality: 0.85 });
}

const image = (url) => ({ type: "image_url", image_url: { url } });
const text = (t) => ({ type: "text", text: t });

/**
 * Mark a structured answer.
 * @param q question record; answerText typed answer; photos array of Blobs (student's working)
 */
export async function mark(q, answerText, photos = []) {
  const { key, model } = await config();
  if (!key) throw new Error("No OpenRouter API key set (Settings).");

  const qImg = await blobToDataURL(await db.get("images", q.image));
  const msBlob = q.msImage && (await db.get("images", q.msImage));
  const content = [
    text(`QUESTION (${q.marks ?? "?"} marks total). Image of the question paper:`),
    image(qImg),
    text(`Extracted question text (may have formatting errors; trust the image):\n${q.text}`),
  ];
  if (msBlob) {
    content.push(text("OFFICIAL MARK SCHEME for this question:"), image(await blobToDataURL(msBlob)));
    if (q.msText) content.push(text(`Extracted mark scheme text:\n${q.msText}`));
  } else {
    content.push(text("No mark scheme is available: mark as an expert examiner would, and say so in the feedback."));
  }
  content.push(text(`STUDENT ANSWER (typed):\n${answerText?.trim() || "(none typed)"}`));
  for (const [i, p] of photos.entries()) {
    content.push(text(`Photo ${i + 1} of the student's written working:`), image(await blobToDataURL(p)));
  }

  const res = await fetch(`${API}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      "X-Title": "A-Level Trainer",
    },
    body: JSON.stringify({
      model,
      max_tokens: 4000,
      messages: [
        { role: "system", content: SYSTEM },
        { role: "user", content },
      ],
      response_format: { type: "json_schema", json_schema: { name: "marking", strict: true, schema: SCHEMA } },
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error?.message ?? `OpenRouter HTTP ${res.status}`);
  const choice = body.choices?.[0];
  if (!choice) throw new Error("Empty response from model");
  const result = parseJSON(choice.message?.content ?? "");
  if (q.marks) result.max = q.marks;
  result.score = Math.max(0, Math.min(result.score, result.max));
  result.model = body.model ?? model;
  result.cost = body.usage?.cost;
  return result;
}

function parseJSON(s) {
  try {
    return JSON.parse(s);
  } catch {
    // models without structured outputs sometimes wrap JSON in prose or fences
    const m = s.match(/\{[\s\S]*\}/);
    if (m) return JSON.parse(m[0]);
    throw new Error("Model did not return valid JSON. Try another model in Settings.");
  }
}
