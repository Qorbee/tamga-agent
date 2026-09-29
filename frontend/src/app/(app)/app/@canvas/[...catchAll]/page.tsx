/**
 * Closes the canvas on the way to any other page. A slot keeps showing what it last rendered when a
 * client-side navigation matches nothing in it, so every other route has to match something empty.
 */
export default function NoCanvas() {
  return null;
}
