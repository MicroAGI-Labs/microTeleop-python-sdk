"""
Base classes and data structures for input providers.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Control goal types."""

    IDLE = "idle"  # No button on vr controller pressed
    GRIP_ACTIVE_INIT = "grip_active_init"  # Grip button pressed first time
    GRIP_ACTIVE = "grip_active"  # Grip button held
    GRIP_RELEASE = "grip_release"  # Grip button released
    TRIGGER_ACTIVE = "trigger_active"  # Trigger button pressed
    TRIGGER_RELEASE = "trigger_release"  # Trigger button released
    RESET_BUTTON_RELEASE = "reset_button_release"  # Reset button released


@dataclass
class VRControllerGoal:
    """Control goal."""

    event_type: EventType = EventType.IDLE
    arm: str = "right"
    origin_transform: Optional[np.ndarray] = None
    target_transform: Optional[np.ndarray] = None
    gripper_closed: Optional[bool] = None


@dataclass
class ArmGoal:
    """Arm goal."""

    arm: str = "right"
    relative_transform: Optional[np.ndarray] = None
    gripper_closed: Optional[bool] = None
    reset_to_init: bool = False
    reset_reference: bool = False


class BaseInputProvider(ABC):
    """Abstract base class for input providers."""

    def __init__(self):
        self.left_queue = asyncio.Queue()
        self.right_queue = asyncio.Queue()
        self.left_gripper_closed = True
        self.right_gripper_closed = True

    @abstractmethod
    async def start(self, *args, **kwargs):
        """Start the input provider."""
        pass

    @abstractmethod
    async def stop(self, *args, **kwargs):
        """Stop the input provider."""
        pass

    async def send_goal(self, goal: VRControllerGoal):
        """Send a control goal."""
        try:
            if goal.arm == "left":
                await self.left_queue.put(goal)
            elif goal.arm == "right":
                await self.right_queue.put(goal)
            else:
                raise ValueError(f"Unknown arm: {goal.arm}")
        except Exception as e:
            # Handle queue full or other errors
            pass

    def get_controller_goal(self, arm: str) -> ArmGoal:
        """Get a control goal from the queue."""
        arm_goal = ArmGoal(arm=arm)
        vr_default_goal = VRControllerGoal(arm=arm)
        vr_goals = []
        last_grip_active_vr_goal = None
        queue = self.left_queue if arm == "left" else self.right_queue
        while not queue.empty():
            vr_goals.append(queue.get_nowait())
        for vr_goal in vr_goals:
            if vr_goal.arm != arm:
                continue
            if vr_goal.event_type == EventType.GRIP_ACTIVE_INIT:
                vr_default_goal.origin_transform = vr_goal.target_transform
                arm_goal.reset_reference = True
            elif vr_goal.event_type == EventType.GRIP_ACTIVE:
                last_grip_active_vr_goal = vr_goal
            elif vr_goal.event_type == EventType.GRIP_RELEASE:
                vr_default_goal.target_transform = None
            elif vr_goal.event_type == EventType.TRIGGER_ACTIVE:
                if arm == "left":
                    self.left_gripper_closed = False
                elif arm == "right":
                    self.right_gripper_closed = False
            elif vr_goal.event_type == EventType.TRIGGER_RELEASE:
                if arm == "left":
                    self.left_gripper_closed = True
                elif arm == "right":
                    self.right_gripper_closed = True
            elif vr_goal.event_type == EventType.RESET_BUTTON_RELEASE:
                # NOTE: When pressing grip right after reset, this may get overwritten and not actually reset
                vr_default_goal.origin_transform = None
                vr_default_goal.target_transform = None
                arm_goal.reset_to_init = True
            else:
                raise ValueError(f"Unknown event type: {vr_goal.event_type}")

        if last_grip_active_vr_goal is not None:
            vr_reference_transform = last_grip_active_vr_goal.origin_transform  # type: ignore
            vr_target_transform = last_grip_active_vr_goal.target_transform  # type: ignore
            relative_transform = np.linalg.inv(vr_reference_transform) @ vr_target_transform

            # Coordinate transform to global VR frame
            transformation_matrix = np.eye(4)
            transformation_matrix[:3, :3] = vr_reference_transform[:3, :3]

            relative_transform = transformation_matrix @ (relative_transform @ np.linalg.inv(transformation_matrix))

            arm_goal.relative_transform = relative_transform

        arm_goal.gripper_closed = self.left_gripper_closed if arm == "left" else self.right_gripper_closed

        return arm_goal
