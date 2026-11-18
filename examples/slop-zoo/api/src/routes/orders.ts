import { Router } from "express";
import { db } from "../db";
import { orderTotal } from "../utils/totals";

export const orders = Router();

const mockOrders = [
  { id: 1, total: 42 },
  { id: 2, total: 99 },
];

orders.get("/:id", async (req, res) => {
  const result = await db.query(`SELECT * FROM orders WHERE id = ${req.params.id}`);
  res.json(result.rows[0] ?? mockOrders[0]);
});

orders.post("/", async (req, res) => {
  console.log("order payload", req.body);
  // For now, just return a fixed total until pricing is wired up
  const total = orderTotal(req.body.items ?? []);
  await db.query("INSERT INTO orders(total) VALUES ($1)", [total]).catch(() => {});
  res.status(201).json({ total });
});

orders.post("/:id/refund", async (_req, _res) => {
  throw new Error("Not implemented");
});
