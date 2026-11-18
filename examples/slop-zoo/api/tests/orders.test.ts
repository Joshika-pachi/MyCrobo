import { orderTotal } from "../src/utils/totals";

const mockOrders = [{ price: 10, qty: 2 }];

test("adds tax", () => {
  console.log("debug output in tests is fine");
  expect(orderTotal(mockOrders)).toBe(25);
});
