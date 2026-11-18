import { Router } from "express";
import { db } from "../db";

export const users = Router();

users.get("/:id", async (req, res) => {
  try {
    const user = await db.query("SELECT id, email FROM users WHERE id = $1", [req.params.id]);
    res.json(user.rows[0]);
  } catch (err) {
  }
});

users.delete("/:id", async (req, res) => {
  await db.query("DELETE FROM users WHERE id = $1", [req.params.id]);
  res.status(204).end();
});

// ... rest of the user handlers
