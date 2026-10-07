import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApprovalStoreError,
  GcsApprovalObjects,
  MAX_LISTED_OBJECTS,
  kmsKeySource,
} from "../src/gcp/approval-store.js";
import { crc32c } from "../src/lib/crc32c.js";
import { approvalKeys } from "./support/approval.js";

// The approval records' Cloud Storage and Cloud KMS adapter (src/gcp/approval-store.ts), against a
// stubbed fetch and a stubbed KMS client: what it asks for, and how it reads every answer.

const token = vi.hoisted((): { value: string | null } => ({ value: "test-access-token" }));

vi.mock("google-auth-library", async (importOriginal) => ({
  ...(await importOriginal<typeof import("google-auth-library")>()),
  GoogleAuth: class {
    public getAccessToken(): Promise<string | null> {
      return Promise.resolve(token.value);
    }
  },
}));

type Sent = { url: string; init: RequestInit };
let sent: Sent[];
let answers: Response[];

beforeEach(() => {
  token.value = "test-access-token";
  sent = [];
  answers = [];
  vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
    sent.push({ url, init });
    init.signal?.throwIfAborted();
    const answer = answers.shift();
    if (answer === undefined) throw new Error("no answer stubbed");
    return Promise.resolve(answer);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("the approval objects", () => {
  it("lists a prefix page by page, with a bearer token", async () => {
    answers.push(json(200, { items: [{ name: "docs/a/1" }], nextPageToken: "next" }));
    answers.push(json(200, { items: [{ name: "docs/a/2" }, { name: 7 }] }));
    const objects = new GcsApprovalObjects("heads");
    expect(await objects.list("docs/a/")).toEqual(["docs/a/1", "docs/a/2"]);
    const first = new URL(sent[0]?.url ?? "");
    expect(first.pathname).toBe("/storage/v1/b/heads/o");
    expect(first.searchParams.get("prefix")).toBe("docs/a/");
    expect(new URL(sent[1]?.url ?? "").searchParams.get("pageToken")).toBe("next");
    expect(new Headers(sent[0]?.init.headers).get("authorization")).toBe(
      "Bearer test-access-token",
    );
  });

  it("refuses a listing Cloud Storage refuses, or one larger than a reader reads", async () => {
    answers.push(json(403, {}));
    await expect(new GcsApprovalObjects("heads").list("docs/")).rejects.toMatchObject({
      name: "ApprovalStoreError",
      httpStatus: 403,
    });
    answers.push(
      json(200, {
        items: Array.from({ length: MAX_LISTED_OBJECTS + 1 }, (_, index) => ({
          name: String(index),
        })),
      }),
    );
    await expect(new GcsApprovalObjects("heads").list("docs/")).rejects.toThrow(
      "A listing holds more objects than a reader reads",
    );
  });

  it("reads an object's bytes, absent as undefined, and refuses anything else", async () => {
    answers.push(new Response("bytes", { status: 200 }));
    expect(await new GcsApprovalObjects("heads").read("docs/a/000000000001")).toBe("bytes");
    expect(sent[0]?.url).toBe(
      "https://storage.googleapis.com/storage/v1/b/heads/o/docs%2Fa%2F000000000001?alt=media",
    );
    answers.push(new Response("", { status: 404 }));
    expect(await new GcsApprovalObjects("heads").read("x")).toBeUndefined();
    answers.push(new Response("", { status: 500 }));
    await expect(new GcsApprovalObjects("heads").read("x")).rejects.toBeInstanceOf(
      ApprovalStoreError,
    );
    answers.push(new Response(new Uint8Array([0xff, 0xfe]), { status: 200 }));
    await expect(new GcsApprovalObjects("heads").read("x")).rejects.toThrow(
      "An approval object is not UTF-8",
    );
  });

  it("creates an object only if absent, and says when it exists", async () => {
    answers.push(json(200, {}));
    expect(await new GcsApprovalObjects("heads").create("docs/a/1", "{}", "application/json")).toBe(
      "created",
    );
    const upload = new URL(sent[0]?.url ?? "");
    expect(upload.pathname).toBe("/upload/storage/v1/b/heads/o");
    expect(upload.searchParams.get("ifGenerationMatch")).toBe("0");
    expect(upload.searchParams.get("name")).toBe("docs/a/1");
    expect(sent[0]?.init.method).toBe("POST");
    expect(new Headers(sent[0]?.init.headers).get("content-type")).toBe("application/json");
    answers.push(json(412, {}));
    expect(await new GcsApprovalObjects("heads").create("docs/a/1", "{}", "application/json")).toBe(
      "exists",
    );
    answers.push(json(403, {}));
    await expect(
      new GcsApprovalObjects("heads").create("docs/a/1", "{}", "application/json"),
    ).rejects.toMatchObject({ httpStatus: 403 });
  });

  it("stops at the caller's signal, and refuses without a credential", async () => {
    const stop = new AbortController();
    stop.abort();
    answers.push(json(200, {}));
    await expect(
      new GcsApprovalObjects("heads").withSignal(stop.signal).read("x"),
    ).rejects.toThrow();
    token.value = null;
    await expect(new GcsApprovalObjects("heads").read("x")).rejects.toThrow(
      "Application Default Credentials returned no access token",
    );
  });
});

describe("the approval key's public keys", () => {
  const KEY = "projects/p/locations/l/keyRings/r/cryptoKeys/approval-signing-hsm";
  const pem = approvalKeys().publicKey.export({ type: "spki", format: "pem" }).toString();

  function client(answer: Record<string, unknown>) {
    const calls: string[] = [];
    return {
      calls,
      getPublicKey: (request: { name: string }) => {
        calls.push(request.name);
        return Promise.resolve([
          {
            name: request.name,
            pem,
            algorithm: "RSA_SIGN_PSS_3072_SHA256",
            pemCrc32c: { value: String(crc32c(Buffer.from(pem, "utf8"))) },
            ...answer,
          },
        ]);
      },
    };
  }

  it("trusts only versions of its own key, and fetches each once", async () => {
    const kms = client({});
    const keys = kmsKeySource(KEY, kms as never);
    expect(await keys(`${KEY}/cryptoKeyVersions/1`)).toBeDefined();
    expect(await keys(`${KEY}/cryptoKeyVersions/1`)).toBeDefined();
    expect(kms.calls).toEqual([`${KEY}/cryptoKeyVersions/1`]);
    expect(await keys(`${KEY}-other/cryptoKeyVersions/1`)).toBeUndefined();
    expect(await keys(KEY)).toBeUndefined();
  });

  it.each([
    ["another algorithm", { algorithm: "RSA_SIGN_PSS_2048_SHA256" }, "another algorithm"],
    ["another version", { name: `${KEY}/cryptoKeyVersions/9` }, "another key version"],
    ["a failed checksum", { pemCrc32c: { value: "1" } }, "fails its checksum"],
    ["no key", { pem: undefined }, "no public key"],
  ])("refuses %s, and asks again next time", async (_case, answer, message) => {
    const kms = client(answer);
    const keys = kmsKeySource(KEY, kms as never);
    await expect(keys(`${KEY}/cryptoKeyVersions/1`)).rejects.toThrow(message);
    await expect(keys(`${KEY}/cryptoKeyVersions/1`)).rejects.toThrow(message);
    expect(kms.calls).toHaveLength(2);
  });
});
