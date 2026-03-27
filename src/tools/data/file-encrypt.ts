/**
 * StackOwl — File Encrypt/Decrypt Tool
 *
 * Encrypts and decrypts files using AES-256-CBC with password-derived keys.
 */

import { readFile, writeFile, access, constants } from "node:fs/promises";
import { resolve, extname } from "node:path";
import {
  randomBytes,
  scryptSync,
  createCipheriv,
  createDecipheriv,
} from "node:crypto";
import type { ToolImplementation, ToolContext } from "../registry.js";

const ALGORITHM = "aes-256-cbc";
const KEY_LENGTH = 32;
const IV_LENGTH = 16;
const SALT_LENGTH = 16;

export const FileEncryptTool: ToolImplementation = {
  definition: {
    name: "file_encrypt",
    description:
      "Encrypt or decrypt files using AES-256 with a password. Encrypted files get .enc extension.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: 'Action: "encrypt" or "decrypt".',
        },
        file_path: {
          type: "string",
          description: "Path to the file to encrypt or decrypt.",
        },
        password: {
          type: "string",
          description: "Password used for encryption/decryption.",
        },
      },
      required: ["action", "file_path", "password"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const action = args["action"] as string;
      const filePath = args["file_path"] as string;
      const password = args["password"] as string;

      if (!action) return "Error: 'action' parameter is required.";
      if (!filePath) return "Error: 'file_path' parameter is required.";
      if (!password) return "Error: 'password' parameter is required.";
      if (action !== "encrypt" && action !== "decrypt") {
        return "Error: 'action' must be 'encrypt' or 'decrypt'.";
      }

      const resolvedPath = resolve(_context.cwd, filePath);

      try {
        await access(resolvedPath, constants.R_OK);
      } catch {
        return `Error: File not found or not readable: ${resolvedPath}`;
      }

      if (action === "encrypt") {
        const data = await readFile(resolvedPath);
        const salt = randomBytes(SALT_LENGTH);
        const key = scryptSync(password, salt, KEY_LENGTH);
        const iv = randomBytes(IV_LENGTH);

        const cipher = createCipheriv(ALGORITHM, key, iv);
        const encrypted = Buffer.concat([cipher.update(data), cipher.final()]);

        // File format: salt (16 bytes) + iv (16 bytes) + encrypted data
        const output = Buffer.concat([salt, iv, encrypted]);
        const outputPath = resolvedPath + ".enc";
        await writeFile(outputPath, output);

        return `File encrypted successfully.\n- Input: ${resolvedPath}\n- Output: ${outputPath}`;
      } else {
        // decrypt
        if (extname(resolvedPath) !== ".enc") {
          return `Warning: File does not have .enc extension. Attempting decryption anyway.`;
        }

        const data = await readFile(resolvedPath);
        if (data.length < SALT_LENGTH + IV_LENGTH + 1) {
          return "Error: File is too small to be a valid encrypted file.";
        }

        const salt = data.subarray(0, SALT_LENGTH);
        const iv = data.subarray(SALT_LENGTH, SALT_LENGTH + IV_LENGTH);
        const encrypted = data.subarray(SALT_LENGTH + IV_LENGTH);

        const key = scryptSync(password, salt, KEY_LENGTH);

        try {
          const decipher = createDecipheriv(ALGORITHM, key, iv);
          const decrypted = Buffer.concat([
            decipher.update(encrypted),
            decipher.final(),
          ]);

          const outputPath = resolvedPath.endsWith(".enc")
            ? resolvedPath.slice(0, -4)
            : resolvedPath + ".decrypted";
          await writeFile(outputPath, decrypted);

          return `File decrypted successfully.\n- Input: ${resolvedPath}\n- Output: ${outputPath}`;
        } catch {
          return "Error: Decryption failed. Wrong password or corrupted file.";
        }
      }
    } catch (error: any) {
      return `Error in file encryption: ${error.message ?? String(error)}`;
    }
  },
};
