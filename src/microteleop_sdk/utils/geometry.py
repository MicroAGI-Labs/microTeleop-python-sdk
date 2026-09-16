import numpy as np


def pose2transform(position: np.ndarray, quaternion: np.ndarray) -> np.ndarray:
    """Convert position and quaternion to a 4x4 homogeneous transformation matrix."""
    x, y, z = position[0], position[1], position[2]
    qx, qy, qz, qw = quaternion[0], quaternion[1], quaternion[2], quaternion[3]

    R = np.array(
        [
            [1 - 2 * (qy**2 + qz**2), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
            [2 * (qx * qy + qz * qw), 1 - 2 * (qx**2 + qz**2), 2 * (qy * qz - qx * qw)],
            [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx**2 + qy**2)],
        ]
    )

    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = [x, y, z]

    return T
