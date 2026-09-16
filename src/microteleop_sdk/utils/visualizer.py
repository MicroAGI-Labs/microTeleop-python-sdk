import threading
import time
from typing import Optional

import numpy as np

try:
    import meshcat
    import meshcat.geometry as g
    import meshcat.transformations as tf

    MESHCAT_AVAILABLE = True
except ImportError:
    MESHCAT_AVAILABLE = False
    print("Warning: meshcat not installed. Install with: pip install meshcat")


class TransformVisualizer:
    """Visualize 4x4 transformation matrices as coordinate frames in the browser using meshcat."""

    def __init__(self):
        """
        Initialize the visualizer.
        """
        if not MESHCAT_AVAILABLE:
            raise ImportError("meshcat is required for visualization. Install with: pip install meshcat")

        self.vis = meshcat.Visualizer()
        self.vis.open()
        self._init_camera_view()

    def _init_camera_view(self):
        path = "/Cameras/default/rotated/<object>"
        v = list([-1, -0.1, 1])
        v[1], v[2] = v[2], -v[1]  # convert to left-handed (x,z,-y)
        self.vis[path].set_property("position", v)

    def _create_coordinate_frame(self, name: str, scale: float = 0.1) -> None:
        """Create a coordinate frame visualization."""
        # X axis (red) - rotate 90 degrees around Z to point along X
        if name == "right_controller":
            y_offset = -scale
        elif name == "left_controller":
            y_offset = scale

        self.vis[f"/{name}/x_axis"].set_object(
            g.Cylinder(height=scale, radius=scale / 20), g.MeshLambertMaterial(color=0xFF0000)
        )
        x_transform = tf.rotation_matrix(np.pi / 2, [0, 0, 1])
        x_transform[0, -1] = scale / 2
        x_transform[1, -1] = y_offset

        self.vis[f"/{name}/x_axis"].set_transform(x_transform)

        # Y axis (green) - rotate 90 degrees around X to point along Y
        self.vis[f"/{name}/y_axis"].set_object(
            g.Cylinder(height=scale, radius=scale / 20), g.MeshLambertMaterial(color=0x00FF00)
        )
        y_transform = np.eye(4)
        y_transform[1, -1] = scale / 2 + y_offset

        self.vis[f"/{name}/y_axis"].set_transform(y_transform)

        # Z axis (blue) - default orientation points up along Z
        self.vis[f"/{name}/z_axis"].set_object(
            g.Cylinder(height=scale, radius=scale / 20), g.MeshLambertMaterial(color=0x0000FF)
        )
        z_transform = tf.rotation_matrix(np.pi / 2, [1, 0, 0])
        z_transform[2, -1] = scale / 2
        z_transform[1, -1] = y_offset
        self.vis[f"/{name}/z_axis"].set_transform(z_transform)

        # Origin sphere
        self.vis[f"/{name}/origin"].set_object(g.Sphere(scale / 10), g.MeshLambertMaterial(color=0xFFFFFF))

        origin_transform = np.eye(4)  # identity matrix
        origin_transform[1, -1] = y_offset

        self.vis[f"/{name}/origin"].set_transform(origin_transform)

    def update_transform(self, name: str, transform: np.ndarray, scale: float = 0.1):
        """
        Update or create a coordinate frame visualization.

        Args:
            name: Name of the coordinate frame
            transform: 4x4 transformation matrix
            scale: Scale of the coordinate frame axes
        """
        if not isinstance(transform, np.ndarray) or transform.shape != (4, 4):
            raise ValueError("Transform must be a 4x4 numpy array")

        # Create the frame if it doesn't exist
        frame_exists = False
        try:
            # Try to check if frame exists by attempting to get its transform
            self.vis[f"/{name}"].get_transform()
            frame_exists = True
        except:
            frame_exists = False

        if not frame_exists:
            self._create_coordinate_frame(name, scale)

        # Update the transform
        self.vis[f"/{name}"].set_transform(transform)

    def update_visualization(
        self,
        left_transform: Optional[np.ndarray] = None,
        right_transform: Optional[np.ndarray] = None,
        scale: float = 0.15,
    ):
        """
        Update VR controller visualizations.

        Args:
            left_transform: 4x4 transform matrix for left controller
            right_transform: 4x4 transform matrix for right controller
            scale: Scale of the coordinate frame axes
        """
        if left_transform is not None:
            self.update_transform("left_controller", left_transform, scale)

        if right_transform is not None:
            self.update_transform("right_controller", right_transform, scale)
