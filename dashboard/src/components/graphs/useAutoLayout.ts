'use client'

import { useCallback } from 'react'
import dagre from 'dagre'
import type { Node, Edge } from '@xyflow/react'

const NODE_WIDTH = 200
const NODE_HEIGHT = 80

interface LayoutOptions {
  direction?: 'TB' | 'LR'
  nodeSep?: number
  rankSep?: number
}

export function useAutoLayout() {
  const layout = useCallback(
    (nodes: Node[], edges: Edge[], options: LayoutOptions = {}) => {
      const { direction = 'TB', nodeSep = 50, rankSep = 80 } = options

      const g = new dagre.graphlib.Graph()
      g.setDefaultEdgeLabel(() => ({}))
      g.setGraph({ rankdir: direction, nodesep: nodeSep, ranksep: rankSep })

      nodes.forEach((node) => {
        g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
      })

      edges.forEach((edge) => {
        g.setEdge(edge.source, edge.target)
      })

      dagre.layout(g)

      const layoutedNodes = nodes.map((node) => {
        const pos = g.node(node.id)
        return {
          ...node,
          position: {
            x: pos.x - NODE_WIDTH / 2,
            y: pos.y - NODE_HEIGHT / 2,
          },
        }
      })

      return { nodes: layoutedNodes, edges }
    },
    [],
  )

  return layout
}
