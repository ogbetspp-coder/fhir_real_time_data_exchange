import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // `.cache/` is scratch space (it is in .gitignore): a file dropped there by a tool or an
    // agent must never be able to fail the quality gate.
    ignores: [
      "dist/**",
      "coverage/**",
      "fhir/vendor/**",
      ".cache/**",
      ".claude/worktrees/**",
      // Zone A's two Python virtualenvs. They are not committed and they are not TypeScript, but
      // a dependency can vendor a stray `.js` (pip vendors one), and the project service then
      // fails on a file that is in no tsconfig. `.uv-bootstrap` holds `uv` itself, which cannot
      // live in `.venv` because `uv sync --frozen` prunes it.
      "zone-a/.venv/**",
      "zone-a/.uv-bootstrap/**",
      // The agent's two virtualenvs, for the same reason.
      "agent/.venv/**",
      "agent/.uv-bootstrap/**",
      // The label reader: Python only, with its own gate (.github/workflows/label-docx-reader.yml).
      "label-docx-reader/**",
    ],
  },
  eslint.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-definitions": ["error", "type"],
      "@typescript-eslint/no-confusing-void-expression": "off",
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
    },
  },
  {
    files: ["**/*.mjs"],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: {
      globals: {
        Buffer: "readonly",
        URL: "readonly",
        console: "readonly",
        fetch: "readonly",
        process: "readonly",
      },
    },
  },
);
