class KickPrediction:
    def __init__(self):
        pass

    def predict(self, ball, power, angle):
        """
        Placeholder kickoff prediction logic.
        Replace with real physics later.
        """
        predicted_x = ball.x + power * 10 * (angle / 90)
        predicted_y = ball.y + power * 10
        return predicted_x, predicted_y
