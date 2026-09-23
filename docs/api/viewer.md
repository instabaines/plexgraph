# Viewing & styling

See [Visualizing it](../user-guide.md#visualizing-it) and [Artistic styling](../user-guide.md#artistic-styling)
in the user guide for the full options table and runnable examples.

## Showing a graph

::: plexgraph.show

::: plexgraph.ShowHandle

## Color, size and shape encodings

These compute a `node_color`/`edge_color`/`node_size`/`edge_width`/`node_shape` value from the
graph itself -- pass the result straight to `show()` or `handle.style()`.

::: plexgraph.by_attribute

::: plexgraph.by_degree

::: plexgraph.by_weight

::: plexgraph.by_time

::: plexgraph.by_time_bucket

::: plexgraph.by_values

::: plexgraph.size_by_attribute

::: plexgraph.size_by_degree

::: plexgraph.size_by_weight

::: plexgraph.size_by_time

::: plexgraph.shape_by_attribute

## Reference data

- `plexgraph.RESET` -- pass as a style option's value (e.g. `node_size=RESET`) to restore that
  option's default, distinct from leaving it out (which means "unchanged").
- `plexgraph.COLORMAPS` -- the colormap names accepted wherever a `cmap=`/`colormap=` argument is
  taken: `viridis plasma inferno magma cividis coolwarm RdBu Spectral Blues Greens Reds Oranges
  Purples Greys YlOrRd`.
- `plexgraph.PALETTES` -- the categorical palette names: `default tab10 Set1 Set2 Dark2 Paired
  Pastel1`.
- `plexgraph.SHAPES` -- the node shape names: `circle square triangle diamond cross`.
- `plexgraph.STYLE_OPTIONS` -- every keyword `show()`/`handle.style()` accepts, including
  matplotlib-style aliases (`edgecolors`, `linewidths`, `font_size`, `font_color`, `style`,
  `with_labels`, `connectionstyle`) -- useful for catching a typo programmatically.
