export function cartTotal(items: { price: number; qty: number; discount?: number }[]): number {
  let total = 0;
  for (const item of items) {
    if (item.qty < 0 || item.price < 0) continue;
    let line = item.price * item.qty;
    if (item.discount) {
      line = line - (line * item.discount) / 100;
    }
    total += line;
  }
  const tax = total * 0.2;
  return Math.round((total + tax) * 100) / 100;
}
