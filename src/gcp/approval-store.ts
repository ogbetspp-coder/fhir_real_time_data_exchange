import type { KeyObject } from "node:crypto";

import { KeyManagementServiceClient } from "@google-cloud/kms";
import { GoogleAuth } from "google-auth-library";

import {
  APPROVAL_KEY_ALGORITHM,
  approvalPublicKey,
  type HeadSource,
  type KeySource,
} from "../approval/statement.js";
import { crc32c } from "../lib/crc32c.js";

// The approval records' Cloud Storage and Cloud KMS, for the three services that hold them: the
// signer writes heads, reviews and statements; the worker and the query service read heads and
// public keys (docs/design/approval.md, D8 and D9). Each caller names its bucket and key; nothing
// here reads a service's configuration (ADR 0004, point 3). The JSON API is called with fetch, so
// every request carries a timeout and, for the query service, the request's own abort signal.

const STORAGE = "https://storage.googleapis.com/storage/v1";
const UPLOAD = "https://storage.googleapis.com/upload/storage/v1";

// How long one Cloud Storage request may take.
export const APPROVAL_STORE_TIMEOUT_MS = 10_000;
// A listing is read in pages of this many names, and at most this many in all: a document with
// more heads than that is refused rather than read without bound.
const LIST_PAGE = 1_000;
export const MAX_LISTED_OBJECTS = 100_000;

export class ApprovalStoreError extends Error {
  public override readonly name = "ApprovalStoreError";

  // `httpStatus` names Cloud Storage's refusal; the body is never carried.
  public constructor(
    message: string,
    public readonly httpStatus?: number,
  ) {
    super(message);
  }
}

export type StoredObjects = HeadSource & {
  // Creates the object unless one exists by that name: `created`, or `exists` when the
  // create-if-absent precondition failed (HTTP 412). Nothing is ever replaced.
  create(name: string, bytes: string, contentType: string): Promise<"created" | "exists">;
};

const auth = new GoogleAuth({ scopes: ["https://www.googleapis.com/auth/devstorage.read_write"] });

export class GcsApprovalObjects implements StoredObjects {
  // `signal`, when given, ends every request once aborted (the query service's request).
  public constructor(
    private readonly bucket: string,
    private readonly signal?: AbortSignal,
  ) {}

  public withSignal(signal: AbortSignal | undefined): GcsApprovalObjects {
    return new GcsApprovalObjects(this.bucket, signal);
  }

  async #fetch(url: string, init: RequestInit = {}): Promise<Response> {
    const timeout = AbortSignal.timeout(APPROVAL_STORE_TIMEOUT_MS);
    const signal = this.signal === undefined ? timeout : AbortSignal.any([this.signal, timeout]);
    const token = await auth.getAccessToken();
    if (token === null || token === undefined) {
      throw new ApprovalStoreError("Application Default Credentials returned no access token");
    }
    const headers = new Headers(init.headers);
    headers.set("authorization", `Bearer ${token}`);
    return fetch(url, { ...init, headers, signal });
  }

  #object(name: string): string {
    return `${STORAGE}/b/${encodeURIComponent(this.bucket)}/o/${encodeURIComponent(name)}`;
  }

  public async list(prefix: string): Promise<string[]> {
    const names: string[] = [];
    let pageToken: string | undefined;
    do {
      const query = new URLSearchParams({
        prefix,
        fields: "items(name),nextPageToken",
        maxResults: String(LIST_PAGE),
      });
      if (pageToken !== undefined) query.set("pageToken", pageToken);
      const response = await this.#fetch(
        `${STORAGE}/b/${encodeURIComponent(this.bucket)}/o?${query.toString()}`,
      );
      if (!response.ok) {
        await response.body?.cancel();
        throw new ApprovalStoreError("Cloud Storage refused a listing", response.status);
      }
      const page = (await response.json()) as {
        items?: { name?: unknown }[];
        nextPageToken?: unknown;
      };
      for (const item of page.items ?? []) {
        if (typeof item.name === "string") names.push(item.name);
      }
      if (names.length > MAX_LISTED_OBJECTS) {
        throw new ApprovalStoreError("A listing holds more objects than a reader reads");
      }
      pageToken = typeof page.nextPageToken === "string" ? page.nextPageToken : undefined;
    } while (pageToken !== undefined);
    return names;
  }

  public async read(name: string): Promise<string | undefined> {
    const response = await this.#fetch(`${this.#object(name)}?alt=media`);
    if (response.status === 404) {
      await response.body?.cancel();
      return undefined;
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new ApprovalStoreError("Cloud Storage refused a read", response.status);
    }
    try {
      return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
        await response.arrayBuffer(),
      );
    } catch {
      throw new ApprovalStoreError("An approval object is not UTF-8");
    }
  }

  public async create(
    name: string,
    bytes: string,
    contentType: string,
  ): Promise<"created" | "exists"> {
    const query = new URLSearchParams({ uploadType: "media", name, ifGenerationMatch: "0" });
    const response = await this.#fetch(
      `${UPLOAD}/b/${encodeURIComponent(this.bucket)}/o?${query.toString()}`,
      { method: "POST", headers: { "content-type": contentType }, body: bytes },
    );
    await response.body?.cancel();
    if (response.status === 412) return "exists";
    if (!response.ok)
      throw new ApprovalStoreError("Cloud Storage refused a write", response.status);
    return "created";
  }
}

// The public key of the one approval key version a reader trusts, fetched once and kept for the
// process's life: a version's public key never changes. Any other version, of this key or another,
// is untrusted (D4, and the amendment of 2026-10-06): a new or compromised version is never trusted
// silently; trusting it is a configuration change (kms_approval_key_version). The algorithm, the
// version and the PEM's checksum are checked, as Cloud KMS's data-integrity guidelines ask.
export function kmsKeySource(
  trustedVersion: string,
  client: Pick<KeyManagementServiceClient, "getPublicKey"> = new KeyManagementServiceClient(),
): KeySource {
  let cached: Promise<KeyObject> | undefined;
  const fetchKey = async (keyVersion: string): Promise<KeyObject> => {
    const [response] = await client.getPublicKey({ name: keyVersion });
    const pem = response.pem;
    if (typeof pem !== "string") throw new ApprovalStoreError("Cloud KMS returned no public key");
    if (response.algorithm !== APPROVAL_KEY_ALGORITHM) {
      throw new ApprovalStoreError("Cloud KMS returned a key of another algorithm");
    }
    if (response.name !== keyVersion) {
      throw new ApprovalStoreError("Cloud KMS returned another key version's public key");
    }
    if (String(response.pemCrc32c?.value) !== String(crc32c(Buffer.from(pem, "utf8")))) {
      throw new ApprovalStoreError("Cloud KMS returned a public key that fails its checksum");
    }
    return approvalPublicKey(pem);
  };
  return async (keyVersion) => {
    if (keyVersion !== trustedVersion) return undefined;
    if (cached === undefined) {
      const key = fetchKey(keyVersion);
      cached = key;
      // A failed fetch is not kept: the next request asks again.
      key.catch(() => {
        if (cached === key) cached = undefined;
      });
    }
    return cached;
  };
}
